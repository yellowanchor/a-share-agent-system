# -*- coding: utf-8 -*-
"""news_fetch.py — 东方财富新闻抓取（批量日更 / 查询时按需补抓）
=================================================================
与 ``data_platform/scripts/05_fetch_news.py`` 的区别：

1. **可 import**：不依赖脚本式 ``main()``，供 ``scripts/update_daily.py``
   与前端"打开个股页自动补抓"复用；
2. **URL 规范化 + 稳定 id**：同一篇文章的不同跟踪参数会折叠成同一个 id，
   与 ``LocalVectorStore`` 的 ``news_+md5(url)[:16]`` 完全一致，便于去重；
3. **as_of 截断**：可只保留指定日期之前的新闻，回测安全（禁止未来函数）。

用法::

    from src.data.collect.news_fetch import fetch_news, fetch_many
    rows = fetch_news("600519", max_pages=1, as_of="2026-11-23")
    rows = fetch_many(["600519", "000001"], max_pages=1, threads=8)
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

logger = logging.getLogger(__name__)

# 东财搜索接口要求带 JSONP 回调（值可固定，服务端不校验）
CALLBACK = "jQuery35101792940631092459_1764599530165"
SEARCH_API = "https://search-api-web.eastmoney.com/search/jsonp"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# 只用于统计的跟踪参数，去重时应剔除
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "spm", "from", "share_token", "code", "_t",
}


def normalize_url(url: str) -> str:
    """规范化 URL：去掉跟踪参数与 fragment，统一小写 host。"""
    url = (url or "").strip()
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in _TRACKING_PARAMS]
    return urlunsplit((
        parts.scheme.lower() or "https",
        parts.netloc.lower(),
        parts.path,
        urlencode(query),
        "",  # 丢弃 fragment
    ))


def news_id(url: str) -> str:
    """稳定 id：与 LocalVectorStore 的 ``_stable_id`` 保持一致。"""
    return "news_" + hashlib.md5(normalize_url(url).encode("utf-8")).hexdigest()[:16]


def _clean(s: str) -> str:
    """清 HTML 标签与多余空白。"""
    s = re.sub(r"<[^>]+>", "", s or "")
    s = s.replace("\u3000", " ").replace("\r", " ").replace("\n", " ")
    return " ".join(s.split())


def fetch_news(
    symbol: str,
    max_pages: int = 1,
    as_of: Optional[str] = None,
    timeout: int = 15,
    retries: int = 2,
) -> list[dict]:
    """抓取单只股票的新闻。

    Args:
        symbol: 6 位股票代码（如 ``"600519"``）。
        max_pages: 最大翻页数，每页最多 100 条；日更用 1 页即可。
        as_of: ``YYYY-MM-DD``，只保留该日 23:59:59 之前的新闻（回测安全）。
        timeout: 单请求超时秒数。
        retries: 单页失败重试次数。

    Returns:
        ``[{title, text, stock_code, source, publish_time, url, id}]``
    """
    from curl_cffi import requests as _requests

    cutoff = f"{as_of} 23:59:59" if as_of else None
    articles: list[dict] = []

    for page in range(1, max(1, max_pages) + 1):
        inner = {
            "uid": "", "keyword": symbol, "type": ["cmsArticleWebOld"],
            "client": "web", "clientType": "web", "clientVersion": "curr",
            "param": {"cmsArticleWebOld": {
                "searchScope": "default", "sort": "default",
                "pageIndex": page, "pageSize": 100,
                "preTag": "<em>", "postTag": "</em>",
            }},
        }
        params = {
            "cb": CALLBACK,
            "param": json.dumps(inner, ensure_ascii=False),
            "_": str(int(time.time() * 1000)),
        }
        headers = {
            "accept": "*/*",
            "referer": f"https://so.eastmoney.com/news/s?keyword={symbol}",
            "user-agent": _UA,
        }

        items: list[dict] = []
        for attempt in range(retries + 1):
            try:
                r = _requests.get(SEARCH_API, params=params, headers=headers,
                                  timeout=timeout, impersonate="chrome")
                text = r.text
                if text.startswith(CALLBACK + "("):
                    text = text[len(CALLBACK) + 1:-1]
                data = json.loads(text)
                items = data.get("result", {}).get("cmsArticleWebOld", []) or []
                break
            except Exception as e:  # noqa: BLE001
                if attempt >= retries:
                    logger.warning("新闻抓取失败 %s page%d: %s", symbol, page, e)
                    items = []
                    break
                time.sleep(0.6 * (attempt + 1))

        if not items:
            break

        for a in items:
            title = _clean(str(a.get("title", "")))
            content = _clean(str(a.get("content", "")))
            merged = f"{title} {content}".strip()
            if len(merged) < 20:
                continue
            publish_time = str(a.get("date", ""))[:19]
            if cutoff and publish_time and publish_time > cutoff:
                continue  # 早于 as_of 的边界之外，丢弃
            url = normalize_url(str(a.get("url", "")))
            if not url:
                continue
            articles.append({
                "title": title[:200],
                "text": merged[:2000],
                "stock_code": symbol,
                "source": "eastmoney",
                "publish_time": publish_time,
                "url": url,
                "id": news_id(url),
            })

        if len(items) < 100:
            break
        time.sleep(0.2)

    return articles


def fetch_many(
    codes: Iterable[str],
    max_pages: int = 1,
    threads: int = 8,
    as_of: Optional[str] = None,
    on_batch: Optional[Callable[[list[dict]], None]] = None,
    log_every: int = 200,
) -> list[dict]:
    """并发抓取多只股票的新闻。

    Args:
        codes: 6 位代码列表。
        max_pages: 每只股票翻页数。
        threads: 并发线程数（东财无严格限流，8 稳妥）。
        as_of: 截止日期，透传给 :func:`fetch_news`。
        on_batch: 每完成一批（默认 500 条）回调，用于落盘/入库，避免内存堆积。
        log_every: 每完成多少只打印一次进度。

    Returns:
        汇总后的新闻列表。
    """
    codes = list(dict.fromkeys(str(c) for c in codes))
    out: list[dict] = []
    done = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=max(1, threads)) as pool:
        futs = {pool.submit(fetch_news, c, max_pages, as_of): c for c in codes}
        for fut in as_completed(futs):
            code = futs[fut]
            try:
                out.extend(fut.result())
            except Exception as e:  # noqa: BLE001
                logger.warning("股票 %s 新闻抓取异常: %s", code, e)
            done += 1
            if done % log_every == 0:
                logger.info("新闻进度 %d/%d，累计 %d 条，用时 %.0fs",
                            done, len(codes), len(out), time.time() - t0)
            if on_batch and len(out) >= 500:
                on_batch(out)
                out = []

    if on_batch and out:
        on_batch(out)
    return out
