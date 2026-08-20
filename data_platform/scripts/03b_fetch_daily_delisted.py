# -*- coding: utf-8 -*-
"""
脚本 03b：退市股日线补采（BaoStock）
====================================
akshare(东财) 不返回已退市股票历史，故用 BaoStock 补采。
BaoStock 服务端响应较慢（~10s/只），但退市股仅 232 只，约 40 分钟。

分片序号从 1000 起，与 akshare 主采版（0-999）不冲突；
进度文件 progress/daily_done.json 与主采版共用（代码级断点续传）。

用法: python scripts/03b_fetch_daily_delisted.py [--limit N]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
from tqdm import tqdm  # noqa: E402

from src.baostock_client import BaoStockClient  # noqa: E402
from src.storage import (  # noqa: E402
    ensure_dirs, RAW_DIR, save_shard, save_progress, load_progress,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

SHARD_SIZE = 100
SHARD_BASE = 1000  # 分片序号段：与 akshare 主采版错开


def main() -> None:
    ap = argparse.ArgumentParser(description="BaoStock 日线补采")
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default="2026-08-20")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--all", action="store_true",
                    help="全量采集(含当前上市股)，默认只采退市股")
    args = ap.parse_args()

    ensure_dirs()
    stock_list = pd.read_parquet(RAW_DIR / "stock_list.parquet")
    if args.all:
        codes = stock_list["code"].tolist()
        logger.info("全量模式: %d 只(含退市股)", len(codes))
    else:
        # 退市股 = 历史期存在但当前不在市
        delisted = stock_list[(stock_list["in_hist"]) & (~stock_list["in_now"])]
        codes = delisted["code"].tolist()
        logger.info("退市股模式: %d 只", len(codes))
    if args.limit:
        codes = codes[:args.limit]
    if not codes:
        logger.info("无待采股票")
        return

    done = load_progress("daily_done")
    todo = [c for c in codes if c not in done]
    logger.info("断点续传: 已完成 %d 只, 剩余 %d 只", len(done), len(todo))
    if not todo:
        return

    buffers: dict[str, list[pd.DataFrame]] = {"hfq": [], "raw": []}
    failed: list[str] = []
    shard_idx = SHARD_BASE
    t0 = time.time()

    with BaoStockClient() as client:
        for i, code in enumerate(tqdm(todo, desc="退市股补采", unit="只")):
            try:
                for name, adj in (("hfq", 1), ("raw", 3)):
                    df = client.get_daily(code, args.start, args.end, adjust=adj)
                    if not df.empty:
                        df["adjust"] = name
                        buffers[name].append(df)
                done.add(code)
            except Exception as e:  # noqa: BLE001
                failed.append(code)
                logger.error("退市股 %s 失败: %s", code, e)

            if (i + 1) % SHARD_SIZE == 0:
                for k in ("hfq", "raw"):
                    if buffers[k]:
                        save_shard(pd.concat(buffers[k], ignore_index=True),
                                   f"daily_{k}", shard_idx)
                        buffers[k] = []
                save_progress("daily_done", done)
                shard_idx += 1

    for k in ("hfq", "raw"):
        if buffers[k]:
            save_shard(pd.concat(buffers[k], ignore_index=True),
                       f"daily_{k}", shard_idx)
    save_progress("daily_done", done)

    if failed:
        with open(RAW_DIR / "daily_failed.json", "w", encoding="utf-8") as f:
            json.dump(failed, f, ensure_ascii=False, indent=2)
        logger.warning("失败 %d 只: %s", len(failed), RAW_DIR / "daily_failed.json")
    logger.info("退市股补采完成: 成功 %d, 失败 %d, 用时 %.1f 分钟",
                len(todo) - len(failed), len(failed), (time.time() - t0) / 60)


if __name__ == "__main__":
    main()
