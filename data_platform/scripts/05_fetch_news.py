# -*- coding: utf-8 -*-
"""
脚本 05：全A股新闻数据采集（情感分析语料）
========================================
数据源：东方财富搜索接口（curl_cffi，多页拉取）—— 老项目已验证一轮 6.1 万条

修正老项目问题：
  1. 去重字段错位：老脚本条目字段为 text/title，但 SimHash 只读 title+content
     → 去重退化为纯标题去重。本脚本对 title+text 计算指纹。
  2. 无断点续传 → 加入进度标记。
  3. 无分片 → 每 500 只一个分片。

字段：title, text(标题+正文合并), stock_code, source, publish_time, url
产出：data/raw/news_partXXX.parquet → data/processed/news.parquet + news.jsonl
用法：python scripts/05_fetch_news.py [--max-pages 2] [--limit 500]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
from tqdm import tqdm  # noqa: E402

from src.storage import (  # noqa: E402
    ensure_dirs, RAW_DIR, PROCESSED_DIR,
    save_shard, save_progress, load_progress,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

SHARD_SIZE = 500

# 与东财搜索接口约定的 JSONP 回调（硬编码值可正常返回）
CALLBACK = "jQuery35101792940631092459_1764599530165"


def fetch_eastmoney_news(symbol: str, max_pages: int = 2) -> list[dict]:
    """从东方财富搜索接口拉取单只股票的新闻（多页）。

    Args:
        symbol: 纯6位股票代码。
        max_pages: 最大翻页数，每页最多100条。

    Returns:
        新闻字典列表 [{title, text, stock_code, source, publish_time, url}]
    """
    from curl_cffi import requests as _requests

    articles: list[dict] = []
    for page in range(1, max_pages + 1):
        inner_param = {
            "uid": "",
            "keyword": symbol,
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientType": "web",
            "clientVersion": "curr",
            "param": {
                "cmsArticleWebOld": {
                    "searchScope": "default",
                    "sort": "default",
                    "pageIndex": page,
                    "pageSize": 100,
                    "preTag": "<em>",
                    "postTag": "</em>",
                }
            },
        }
        params = {
            "cb": CALLBACK,
            "param": json.dumps(inner_param, ensure_ascii=False),
            "_": str(int(time.time() * 1000)),
        }
        headers = {
            "accept": "*/*",
            "referer": f"https://so.eastmoney.com/news/s?keyword={symbol}",
            "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0 Safari/537.36"),
        }
        try:
            r = _requests.get(
                "https://search-api-web.eastmoney.com/search/jsonp",
                params=params, headers=headers, timeout=15,
            )
            text = r.text
            if text.startswith(CALLBACK + "("):
                text = text[len(CALLBACK) + 1:-1]
            data = json.loads(text)
            items = data.get("result", {}).get("cmsArticleWebOld", [])
            if not items:
                break
            for a in items:
                title = _clean(str(a.get("title", "")))
                content = _clean(str(a.get("content", "")))
                merged = f"{title} {content}".strip()
                if len(merged) < 20:
                    continue
                articles.append({
                    "title": title[:200],
                    "text": merged[:2000],
                    "stock_code": symbol,
                    "source": "eastmoney",
                    "publish_time": str(a.get("date", ""))[:19],
                    "url": str(a.get("url", "")),
                })
            if len(items) < 100:
                break
            time.sleep(0.2)
        except Exception as e:  # noqa: BLE001
            logger.debug("股票 %s 第%d页失败: %s", symbol, page, e)
            break
    return articles


def _clean(s: str) -> str:
    """清理标题/正文中的 HTML 标签与多余空白"""
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("\u3000", " ").replace("\r", " ").replace("\n", " ")
    return " ".join(s.split())


def _simhash(text: str, bits: int = 64) -> int:
    """64位 SimHash 指纹（中文2-gram + 英文分词，词频加权）"""
    tokens: list[str] = []
    ch = re.findall(r"[\u4e00-\u9fff]", text)
    tokens.extend(ch[i] + ch[i + 1] for i in range(len(ch) - 1))
    tokens.extend(t.lower() for t in re.findall(r"[a-zA-Z0-9]+", text) if len(t) >= 2)
    if not tokens:
        return 0
    freq: dict[str, int] = {}
    for t in tokens:
        freq[t] = freq.get(t, 0) + 1
    v = [0] * bits
    for w, f in freq.items():
        h = int(hashlib.md5(w.encode()).hexdigest()[:16], 16)
        for i in range(bits):
            v[i] += f if (h >> i) & 1 else -f
    fp = 0
    for i in range(bits):
        if v[i] > 0:
            fp |= 1 << i
    return fp


def dedup(df: pd.DataFrame, threshold: int = 3, n_blocks: int = 4) -> pd.DataFrame:
    """
    跨源去重：对 title+text 计算 SimHash，汉明距离<=阈值视为重复。
    【性能修复 2026-08-20】原实现为 O(n²) 暴力比较，52.9 万条会卡死数小时。
    现改为分桶索引：64 位指纹切 n_blocks 段，汉明距离<=threshold(<n_blocks)的
    两个指纹必有一段完全相同，故仅需在同段桶内比较，复杂度 O(n)。
    """
    if df.empty:
        return df
    block_bits = 64 // n_blocks
    mask = (1 << block_bits) - 1
    buckets: dict[tuple[int, int], list[tuple[int, int]]] = {}
    keep_idx: list[int] = []
    for i, row in df.iterrows():
        fp = _simhash(str(row.get("title", "")) + str(row.get("text", "")))
        if fp == 0:
            keep_idx.append(i)
            continue
        dup = False
        for seg in range(n_blocks):
            seg_val = (fp >> (seg * block_bits)) & mask
            for old_fp, _ in buckets.get((seg, seg_val), ()):
                if (fp ^ old_fp).bit_count() <= threshold:
                    dup = True
                    break
            if dup:
                break
        if dup:
            continue
        keep_idx.append(i)
        for seg in range(n_blocks):
            seg_val = (fp >> (seg * block_bits)) & mask
            buckets.setdefault((seg, seg_val), []).append((fp, i))
    out = df.iloc[keep_idx].reset_index(drop=True)
    logger.info("SimHash 去重: %d -> %d", len(df), len(out))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="全A股新闻采集")
    ap.add_argument("--max-pages", type=int, default=2, help="每只股票翻页数")
    ap.add_argument("--limit", type=int, default=0, help="只采集前N只(调试用, 0=全部)")
    args = ap.parse_args()

    ensure_dirs()
    stock_list = pd.read_parquet(RAW_DIR / "stock_list.parquet")
    codes = stock_list["code"].str.split(".").str[1].tolist()
    if args.limit:
        codes = codes[:args.limit]
    logger.info("待采集股票数: %d (每只最多 %d 页)", len(codes), args.max_pages)

    done = load_progress("news_done")
    todo = [c for c in codes if c not in done]
    logger.info("断点续传: 已完成 %d 只, 剩余 %d 只", len(done), len(todo))
    if not todo:
        logger.info("全部已完成")
        return

    buffers: list[pd.DataFrame] = []
    shard_idx = len(done) // SHARD_SIZE

    for i, code in enumerate(tqdm(todo, desc="采集新闻", unit="只")):
        try:
            arts = fetch_eastmoney_news(code, max_pages=args.max_pages)
            if arts:
                buffers.append(pd.DataFrame(arts))
        except Exception as e:  # noqa: BLE001
            logger.error("股票 %s 采集失败: %s", code, e)
        done.add(code)

        if (i + 1) % SHARD_SIZE == 0:
            if buffers:
                save_shard(pd.concat(buffers, ignore_index=True), "news", shard_idx)
                buffers = []
            save_progress("news_done", done)
            shard_idx += 1

    if buffers:
        save_shard(pd.concat(buffers, ignore_index=True), "news", shard_idx)
    save_progress("news_done", done)

    # 合并 + 去重 + 输出
    parts = sorted(RAW_DIR.glob("news_part*.parquet"))
    df = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    logger.info("合并原始新闻: %d 条", len(df))
    df = dedup(df)
    df = df.drop_duplicates(subset=["url"]).drop_duplicates(subset=["text"])
    out = PROCESSED_DIR / "news.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    # 同时输出 JSONL（老项目训练脚本兼容格式）
    with open(PROCESSED_DIR / "news.jsonl", "w", encoding="utf-8") as f:
        for _, row in df.iterrows():
            rec = {"text": row["text"], "stock_code": row["stock_code"],
                   "source": row["source"], "publish_time": row["publish_time"],
                   "title": row["title"], "label": None}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    logger.info("最终新闻数据集: %d 条 (%s)", len(df), out)


if __name__ == "__main__":
    main()
