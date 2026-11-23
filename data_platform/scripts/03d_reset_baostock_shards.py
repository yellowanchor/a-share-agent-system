# -*- coding: utf-8 -*-
"""
脚本 03d：混源修复 — 移除 BaoStock 误采分片
============================================
背景：03b 曾被以 --all 模式误启动，用 BaoStock 采了 stock_list 前 100 只
（写入 daily_hfq/daily_raw_part1000.parquet，并记入 daily_done.json）。
为统一数据源（全市场新浪 + 全历史 + hfq_factor），本脚本：

1. 将 part1000 分片备份到 data/raw/_backup/
2. 从 daily_done.json 移除这 100 个 code
3. 之后重跑 03c_fetch_daily_sina.py 即可自动补采（断点续传）

用法: python scripts/03d_reset_baostock_shards.py
"""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BASE = Path(__file__).resolve().parent.parent
RAW_DIR = BASE / "data" / "raw"
PROGRESS_DIR = BASE / "data" / "progress"
BACKUP_DIR = RAW_DIR / "_backup"


def main() -> None:
    # 1. 读取 part1000 的股票列表
    p1_path = RAW_DIR / "daily_hfq_part1000.parquet"
    if not p1_path.exists():
        logger.warning("part1000 不存在，无需处理")
        return
    codes = sorted(pd.read_parquet(p1_path, columns=["code"])["code"].unique())
    logger.info("part1000 共 %d 只股票", len(codes))

    # 2. 备份分片
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("daily_hfq_part1000.parquet", "daily_raw_part1000.parquet"):
        src = RAW_DIR / name
        if src.exists():
            dst = BACKUP_DIR / name
            shutil.move(str(src), str(dst))
            logger.info("已备份: %s -> %s", name, BACKUP_DIR / name)

    # 3. 从 daily_done.json 移除这些 code
    prog = PROGRESS_DIR / "daily_done.json"
    done = json.loads(prog.read_text(encoding="utf-8"))
    removed = [c for c in codes if c in done]
    new_done = [c for c in done if c not in set(codes)]
    prog.write_text(json.dumps(new_done, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("daily_done: %d -> %d（移除 %d 只）", len(done), len(new_done), len(removed))

    logger.info("完成！现在可重跑: python scripts/03c_fetch_daily_sina.py")


if __name__ == "__main__":
    main()
