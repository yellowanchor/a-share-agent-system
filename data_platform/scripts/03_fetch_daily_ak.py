# -*- coding: utf-8 -*-
"""
脚本 03：全A股日线批量采集 —— akshare(东财) 主采版
==================================================
数据源：AkShare stock_zh_a_hist（东方财富，单只 0.4s，支持多线程并发）
适用：当前上市 A 股（东财接口不返回已退市股票）

采集内容：每只股票两套价格
  - 后复权(hfq)：连续可比，机器学习训练标准口径
  - 不复权(raw)：原始成交价
字段：open/high/low/close/volume/amount/振幅/涨跌幅/涨跌额/换手率

工程特性：
  - ThreadPoolExecutor 8 线程并发（东财接口无登录态，可并发）
  - 断点续传：progress/daily_done.json（与退市股补采脚本共用）
  - 分片存储：每 1000 只一个分片 daily_{hfq,raw}_part{idx}.parquet
  - 失败重试3次，最终失败记入 daily_failed.json

用法：python scripts/03_fetch_daily_ak.py [--limit N] [--threads 8]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
from tqdm import tqdm  # noqa: E402

from src.storage import (  # noqa: E402
    ensure_dirs, RAW_DIR, save_shard, save_progress, load_progress,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

SHARD_SIZE = 1000

# akshare 中文列名 → 规范英文列名
COLUMN_MAP = {
    "日期": "date",
    "股票代码": "symbol",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",       # 手（1手=100股）
    "成交额": "amount",       # 元
    "振幅": "amplitude",      # %
    "涨跌幅": "pct_chg",      # %
    "涨跌额": "chg",          # 元
    "换手率": "turn",         # %
}


def fetch_one(symbol: str, start: str, end: str) -> dict[str, pd.DataFrame]:
    """拉取单只股票 hfq + raw 两套日线"""
    import akshare as ak

    out = {}
    for name, adjust in (("hfq", "hfq"), ("raw", "")):
        for attempt in range(3):
            try:
                df = ak.stock_zh_a_hist(
                    symbol=symbol, period="daily",
                    start_date=start, end_date=end, adjust=adjust,
                )
                if df is None or df.empty:
                    break
                df = df.rename(columns=COLUMN_MAP)
                df = df[["date", "open", "high", "low", "close",
                         "volume", "amount", "amplitude",
                         "pct_chg", "chg", "turn"]].copy()
                df["symbol"] = symbol
                df["adjust"] = name
                df["date"] = df["date"].astype(str)
                out[name] = df
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    raise
                time.sleep(1.5 * (attempt + 1))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="全A股日线批量采集(akshare)")
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default="2026-08-20")
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="只采集前N只(调试, 0=全部)")
    args = ap.parse_args()
    start_fmt = args.start.replace("-", "")
    end_fmt = args.end.replace("-", "")

    ensure_dirs()
    stock_list = pd.read_parquet(RAW_DIR / "stock_list.parquet")
    # 只采当前上市股（退市股由 03b 用 BaoStock 补）
    symbols = stock_list[stock_list["in_now"]]["symbol"].tolist()
    if args.limit:
        symbols = symbols[:args.limit]
    logger.info("待采集(当前上市): %d 只, 线程 %d", len(symbols), args.threads)

    done = load_progress("daily_done")
    todo = [s for s in symbols if s not in done]
    logger.info("断点续传: 已完成 %d 只, 剩余 %d 只", len(done), len(todo))
    if not todo:
        logger.info("全部已完成")
        return

    buffers: dict[str, list[pd.DataFrame]] = {"hfq": [], "raw": []}
    failed: list[str] = []
    shard_idx = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=args.threads) as pool:
        futs = {pool.submit(fetch_one, s, start_fmt, end_fmt): s for s in todo}
        with tqdm(total=len(todo), desc="采集日线", unit="只") as bar:
            for fut in as_completed(futs):
                s = futs[fut]
                try:
                    res = fut.result()
                    for k in ("hfq", "raw"):
                        if k in res:
                            buffers[k].append(res[k])
                    done.add(s)
                except Exception as e:  # noqa: BLE001
                    failed.append(s)
                    logger.error("股票 %s 失败: %s", s, str(e)[:100])
                bar.update(1)
                bar.set_postfix(ok=len(done) - len(failed), fail=len(failed))

                if len(done) % SHARD_SIZE == 0:
                    for k in ("hfq", "raw"):
                        if buffers[k]:
                            save_shard(pd.concat(buffers[k], ignore_index=True),
                                       f"daily_{k}", shard_idx)
                            buffers[k] = []
                    save_progress("daily_done", done)
                    shard_idx += 1

    # 收尾 flush
    for k in ("hfq", "raw"):
        if buffers[k]:
            save_shard(pd.concat(buffers[k], ignore_index=True),
                       f"daily_{k}", shard_idx)
    save_progress("daily_done", done)

    if failed:
        with open(RAW_DIR / "daily_failed.json", "w", encoding="utf-8") as f:
            json.dump(failed, f, ensure_ascii=False, indent=2)
        logger.warning("失败 %d 只: %s", len(failed), RAW_DIR / "daily_failed.json")
    logger.info("完成: 成功 %d, 失败 %d, 用时 %.1f 分钟",
                len(todo) - len(failed), len(failed), (time.time() - t0) / 60)


if __name__ == "__main__":
    main()
