# -*- coding: utf-8 -*-
"""
脚本 03：全A股日线批量采集 —— 腾讯行情源（主采版）
===================================================
数据源：腾讯 web.ifzq.gtimg.cn（免费、稳定、无登录、**支持退市股历史**）
特性：
  - 单次最多返回 640 根 K 线 → 10年数据按 end 日期前移分段拉取
  - 后复权(hfq) + 不复权(raw) 两套价格
  - 6 线程并发（腾讯接口无会话状态，可并发）
  - 断点续传 + 分片存储 + 失败清单

腾讯日线字段顺序: [date, open, close, high, low, volume(手)]
统一转换为标准: date, open, high, low, close, volume(股)

用法：python scripts/03_fetch_daily_tencent.py [--limit N] [--threads 6]
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
SHARD_BASE = 2000          # 分片序号段，避免与其他采集源冲突
TENCENT_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0 Safari/537.36"),
    "Referer": "https://gu.qq.com/",
}
SEGMENT = 640              # 腾讯单次返回上限


def _fetch_segment(symbol: str, start: str, end: str, fq: str) -> list[list]:
    """拉取一段 K 线（end 往前最多 640 条），带退避重试"""
    import requests

    fq_param = "hfq" if fq == "hfq" else ""
    params = {"param": f"{symbol},day,{start},{end},{SEGMENT},{fq_param}"}
    for attempt in range(4):
        try:
            r = requests.get(TENCENT_URL, params=params, headers=HEADERS, timeout=15)
            j = r.json()
            d = j.get("data") or {}
            if not d:
                return []
            obj = list(d.values())[0]
            key = "hfqday" if fq == "hfq" else "day"
            return obj.get(key) or []
        except Exception as e:  # noqa: BLE001
            if attempt == 3:
                raise
            time.sleep(1.5 * (2 ** attempt))  # 1.5s / 3s / 6s 退避
    return []


def fetch_symbol(symbol: str, start: str, end: str, fq: str) -> pd.DataFrame:
    """分段拉取单只股票完整历史，返回规范 DataFrame"""
    all_blocks: list[list[list]] = []
    cur_end = end
    guard = 0
    while True:
        rows = _fetch_segment(symbol, start, cur_end, fq)
        if not rows:
            break
        all_blocks.append(rows)
        first_date = rows[0][0]
        if first_date <= start:
            break
        cur_end = (pd.Timestamp(first_date) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        guard += 1
        if guard > 20:  # 防御：最多 20 段
            break
        time.sleep(0.2)

    # 合并（逆序拼接，按日期去重）
    merged: list[list] = []
    seen: set[str] = set()
    for block in reversed(all_blocks):
        for row in block:
            if row[0] not in seen:
                seen.add(row[0])
                merged.append(row)
    if not merged:
        return pd.DataFrame()

    # 腾讯字段顺序: date, open, close, high, low, volume(手)
    # 部分股票行尾带额外字段 → 统一截取前 6 列
    df = pd.DataFrame([r[:6] for r in merged],
                      columns=["date", "open", "close", "high", "low", "volume"])
    df = df[["date", "open", "high", "low", "close", "volume"]].copy()
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["volume"] = df["volume"] * 100        # 手 → 股
    df["symbol"] = symbol
    df["adjust"] = fq
    return df


def fetch_one(symbol: str, start: str, end: str) -> dict[str, pd.DataFrame]:
    """拉取单只股票 hfq + raw 两套"""
    out = {}
    for fq in ("hfq", "raw"):
        for attempt in range(3):
            try:
                df = fetch_symbol(symbol, start, end, fq)
                if not df.empty:
                    out[fq] = df
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    raise
                time.sleep(1.5 * (attempt + 1))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="全A股日线批量采集(腾讯)")
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default="2026-08-20")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    ensure_dirs()
    stock_list = pd.read_parquet(RAW_DIR / "stock_list.parquet")
    # sh.600519 → sh600519
    codes = stock_list["code"].str.replace(".", "", regex=False).tolist()
    if args.limit:
        codes = codes[:args.limit]
    logger.info("待采集: %d 只(含退市股), 线程 %d", len(codes), args.threads)

    done = load_progress("daily_done")
    todo = [c for c in codes if c not in done]
    logger.info("断点续传: 已完成 %d 只, 剩余 %d 只", len(done), len(todo))
    if not todo:
        logger.info("全部已完成")
        return

    buffers: dict[str, list[pd.DataFrame]] = {"hfq": [], "raw": []}
    failed: list[str] = []
    shard_idx = SHARD_BASE
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=args.threads) as pool:
        futs = {pool.submit(fetch_one, c, args.start, args.end): c for c in todo}
        with tqdm(total=len(todo), desc="采集日线", unit="只") as bar:
            for fut in as_completed(futs):
                c = futs[fut]
                try:
                    res = fut.result()
                    for k in ("hfq", "raw"):
                        if k in res:
                            buffers[k].append(res[k])
                    done.add(c)
                except Exception as e:  # noqa: BLE001
                    failed.append(c)
                    logger.error("股票 %s 失败: %s", c, str(e)[:100])
                bar.update(1)

                if len(done) % SHARD_SIZE == 0:
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
    logger.info("完成: 成功 %d, 失败 %d, 用时 %.1f 分钟",
                len(todo) - len(failed), len(failed), (time.time() - t0) / 60)


if __name__ == "__main__":
    main()
