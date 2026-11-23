# -*- coding: utf-8 -*-
"""news_live.py — L2 按需新闻抓取（用户问某只股票时现场拉最新新闻）
========================================================================

为什么需要它
------------
RAG 向量库里的新闻是"每日批量池"的产物，最多滞后一天；而且只覆盖
流动性/异动池内的股票。用户问一只池外股票时，库里根本没有它的新闻。

本模块在"问询路径"上做一次**单只股票的现场抓取**：

* 只抓最新 1–2 页（东财搜索接口，单只约 1–3 秒）；
* 按 URL 归一化后的 id 去重（与 LocalVectorStore 的 `news_<md5>` 一致）；
* 结果落本地缓存 ``data/cache/live_news/<代码>_<YYYYMMDD>.json``，默认 6 小时内
  复用（同一只股票反复问不会重复打接口）；
* **历史 as_of 直接返回空**：回测/复现时绝不能看到"现在"的新闻，这是 PIT 铁律。

用法::

    from src.data.collect.news_live import live_news
    items = live_news("600519", as_of=None)      # 最新
    items = live_news("600519", as_of="2026-08-20")  # 返回 []（历史不复现未来新闻）
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

from src.data.collect.news_fetch import fetch_news, news_id, normalize_url

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]
CACHE_DIR = ROOT / "data" / "cache" / "live_news"


def _cache_path(six: str) -> Path:
    return CACHE_DIR / f"{six}_{date.today().strftime('%Y%m%d')}.json"


def _load_cache(six: str, ttl_hours: float) -> Optional[List[Dict]]:
    p = _cache_path(six)
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    ts = float(obj.get("ts") or 0)
    if time.time() - ts > ttl_hours * 3600:
        return None
    return obj.get("items") or []


def _save_cache(six: str, items: List[Dict]) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(six).write_text(
            json.dumps({"ts": time.time(), "items": items}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("写缓存失败 %s: %s", six, exc)


def live_news(
    code: str,
    as_of: Optional[str] = None,
    max_pages: int = 1,
    ttl_hours: float = 6.0,
    force: bool = False,
) -> List[Dict]:
    """抓某只股票的最新新闻（带缓存）。

    Args:
        code: ``600519`` 或 ``sh.600519``。
        as_of: 分析基准日。**只要早于今天就直接返回空列表**（避免未来函数）；
            等于今天或 None 时抓最新新闻。
        max_pages: 翻页数（1 页 100 条足够）。
        ttl_hours: 缓存有效期（小时）。
        force: True 时忽略缓存强制重抓。

    Returns:
        ``[{id, title, text, source, publish_time, url, stock_code, live: True}]``，
        按发布时间降序；抓取失败返回空列表（不抛异常，调用方继续走 RAG）。
    """
    six = code.split(".")[-1]
    today = date.today().isoformat()
    if as_of:
        as_of_day = str(as_of)[:10]
        if as_of_day < today:
            logger.debug("历史 as_of=%s，跳过 L2 现场抓取（PIT）", as_of_day)
            return []

    if not force:
        cached = _load_cache(six, ttl_hours)
        if cached is not None:
            return cached

    try:
        df = fetch_news(six, max_pages=max_pages, as_of=None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("L2 抓取失败 %s: %s", six, str(exc)[:100])
        return []

    items: List[Dict] = []
    if df is None:
        rows = []
    elif hasattr(df, "to_dict"):
        rows = df.to_dict("records")
    else:
        rows = list(df)
    if rows:
        seen = set()
        for row in rows:
            url = normalize_url(str(row.get("url") or ""))
            nid = news_id(url) if url else ""
            key = nid or f"{row.get('title')}|{row.get('publish_time')}"
            if key in seen:
                continue
            seen.add(key)
            items.append({
                "id": nid or key,
                "title": str(row.get("title") or ""),
                "text": str(row.get("text") or ""),
                "source": str(row.get("source") or ""),
                "publish_time": str(row.get("publish_time") or "")[:19],
                "url": url,
                "stock_code": six,
                "live": True,
            })
    items.sort(key=lambda x: x.get("publish_time") or "", reverse=True)
    _save_cache(six, items)
    logger.info("L2 现场抓取 %s: %d 条（缓存 %s）", six, len(items),
                datetime.now().strftime("%H:%M"))
    return items
