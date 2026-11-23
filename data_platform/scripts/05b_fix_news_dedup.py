# -*- coding: utf-8 -*-
"""
脚本 05b：新闻数据高效去重合并（修复 O(n²) 性能问题）
========================================================
原 05_fetch_news.py 的 dedup() 采用暴力两两比较（52.9万条 → 万亿次运算，卡死）。
本脚本改用 SimHash 分桶索引：
  64 位指纹切 4 段（16 位/段），汉明距离 <=3 的两个指纹至少 1 段完全相同，
  因此只需在相同段值的桶内比较，复杂度 O(n)，52.9 万条约 10 秒完成。

用法: python scripts/05b_fix_news_dedup.py
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from src.storage import RAW_DIR, PROCESSED_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _simhash(text: str, bits: int = 64) -> int:
    """64位 SimHash 指纹（与 05_fetch_news.py 保持一致）"""
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


def dedup_fast(df: pd.DataFrame, threshold: int = 3, n_blocks: int = 4) -> pd.DataFrame:
    """
    分桶 SimHash 去重。
    原理：64 位指纹切成 n_blocks 段，汉明距离 <= threshold 的两个指纹
    必有一段完全相同（threshold < n_blocks 时成立），故只需在同段桶内比较。
    """
    if df.empty:
        return df
    block_bits = 64 // n_blocks          # 每段位数
    mask = (1 << block_bits) - 1         # 段掩码
    # buckets[(seg_idx, seg_value)] = [(fp, row_index)]
    buckets: dict[tuple[int, int], list[tuple[int, int]]] = {}
    keep_idx: list[int] = []

    for i, row in df.iterrows():
        fp = _simhash(str(row.get("title", "")) + str(row.get("text", "")))
        if fp == 0:
            keep_idx.append(i)           # 无有效 token（如空文本），保守保留
            continue
        dup = False
        # 检查 4 个段，任一桶内存在汉明距离<=threshold 则判重
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
    logger.info("SimHash 分桶去重: %d -> %d", len(df), len(out))
    return out


def main() -> None:
    parts = sorted(RAW_DIR.glob("news_part*.parquet"))
    if not parts:
        logger.error("无新闻分片")
        return
    df = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    logger.info("合并原始新闻: %d 条 (%d 个分片)", len(df), len(parts))

    # 1. SimHash 分桶去重（近重复，如转载改写）
    df = dedup_fast(df)
    # 2. 精确去重：url 与 text
    n0 = len(df)
    df = df.drop_duplicates(subset=["url"]).drop_duplicates(subset=["text"])
    logger.info("url/text 精确去重: %d -> %d", n0, len(df))

    # 3. 输出 parquet + jsonl
    out = PROCESSED_DIR / "news.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    with open(PROCESSED_DIR / "news.jsonl", "w", encoding="utf-8") as f:
        for _, row in df.iterrows():
            rec = {"text": row["text"], "stock_code": row["stock_code"],
                   "source": row["source"], "publish_time": row["publish_time"],
                   "title": row["title"], "label": None}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    logger.info("最终新闻数据集: %d 条 (%s)", len(df), out)


if __name__ == "__main__":
    main()
