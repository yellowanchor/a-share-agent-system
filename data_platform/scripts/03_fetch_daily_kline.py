# -*- coding: utf-8 -*-
"""
脚本 03：全A股 10 年日线批量采集（多进程版）
============================================
数据源：BaoStock（免费、历史完整、含退市股）
采集内容：每只股票两套价格
  - 后复权(hfq)：连续可比，机器学习训练标准口径（无未来函数）
  - 不复权(raw)：原始成交价，用于复权校验与实盘对齐
附带字段：换手率、涨跌幅、PE_TTM、PB、PS、停牌标记、ST 标记

多进程设计（BaoStock 线程不安全但进程安全）：
  - 每个 worker 进程独立登录、独立连接，互不干扰
  - 默认 4 进程，速度约为单线程的 3~4 倍
  - 每个 worker 维护独立的分片序号段（w*1000 起），避免文件冲突

工程特性：
  - 断点续传：已完成代码写入 progress/daily_done.json，中断重跑自动跳过
  - 失败记录：重试3次仍失败记入 daily_failed.json，便于事后补采

用法：
  python scripts/03_fetch_daily_kline.py            # 4进程全量
  python scripts/03_fetch_daily_kline.py --workers 2 --limit 20   # 快速试跑
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from multiprocessing import Pool
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

SHARD_SIZE = 500          # 每 500 只股票一个分片
ADJUST_FLAG = {"hfq": 1, "raw": 3}


def fetch_one(client: BaoStockClient, code: str,
              start: str, end: str) -> dict[str, pd.DataFrame]:
    """拉取单只股票两套价格（hfq + raw）"""
    out = {}
    for name, adj in ADJUST_FLAG.items():
        df = client.get_daily(code, start, end, adjust=adj)
        if not df.empty:
            df["adjust"] = name
            out[name] = df
    return out


def worker(args: tuple) -> dict:
    """
    单进程 worker：处理一段股票代码。
    args: (worker_id, codes, start, end, shard_size)
    返回: {"done": [...], "failed": [...], "shards": 分片数}
    """
    worker_id, codes, start, end, shard_size = args
    client = BaoStockClient()
    client.login()

    buffers: dict[str, list[pd.DataFrame]] = {"hfq": [], "raw": []}
    done: list[str] = []
    failed: list[str] = []
    # 分片序号段：worker w 使用 [w*1000, w*1000+999]
    shard_idx = worker_id * 1000

    for code in tqdm(codes, desc=f"W{worker_id}", unit="只",
                     position=worker_id, leave=False):
        try:
            res = fetch_one(client, code, start, end)
            for k in ("hfq", "raw"):
                if k in res:
                    buffers[k].append(res[k])
            done.append(code)
        except Exception as e:  # noqa: BLE001
            failed.append(code)
            logger.error("W%d 股票 %s 失败: %s", worker_id, code, e)

        if len(done) % shard_size == 0:
            for k in ("hfq", "raw"):
                if buffers[k]:
                    save_shard(pd.concat(buffers[k], ignore_index=True),
                               f"daily_{k}", shard_idx)
                    buffers[k] = []
            shard_idx += 1

    # 收尾 flush
    for k in ("hfq", "raw"):
        if buffers[k]:
            save_shard(pd.concat(buffers[k], ignore_index=True),
                       f"daily_{k}", shard_idx)
    client.logout()
    return {"done": done, "failed": failed}


def main() -> None:
    ap = argparse.ArgumentParser(description="全A股日线批量采集（多进程）")
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default="2026-08-20")
    ap.add_argument("--workers", type=int, default=4, help="进程数(默认4)")
    ap.add_argument("--limit", type=int, default=0, help="只采集前N只(调试, 0=全部)")
    args = ap.parse_args()

    ensure_dirs()
    stock_list = pd.read_parquet(RAW_DIR / "stock_list.parquet")
    codes = stock_list["code"].tolist()
    if args.limit:
        codes = codes[:args.limit]
    logger.info("待采集股票数: %d, 进程数: %d", len(codes), args.workers)

    done = load_progress("daily_done")
    todo = [c for c in codes if c not in done]
    logger.info("断点续传: 已完成 %d 只, 剩余 %d 只", len(done), len(todo))
    if not todo:
        logger.info("全部已完成")
        return

    # 均匀切分给各 worker
    chunks = [todo[i::args.workers] for i in range(args.workers)]
    chunks = [c for c in chunks if c]  # 去掉空块
    tasks = [(i, c, args.start, args.end, SHARD_SIZE)
             for i, c in enumerate(chunks)]

    t0 = time.time()
    all_done, all_failed = [], []
    with Pool(args.workers) as pool:
        for res in pool.imap_unordered(worker, tasks):
            all_done.extend(res["done"])
            all_failed.extend(res["failed"])
            save_progress("daily_done", set(done) | set(all_done))
            logger.info("进度: 成功 %d / 失败 %d", len(all_done), len(all_failed))

    # 最终进度与失败清单
    save_progress("daily_done", set(done) | set(all_done))
    if all_failed:
        with open(RAW_DIR / "daily_failed.json", "w", encoding="utf-8") as f:
            json.dump(all_failed, f, ensure_ascii=False, indent=2)
        logger.warning("采集失败 %d 只, 清单: %s", len(all_failed),
                       RAW_DIR / "daily_failed.json")
    logger.info("全部完成: 成功 %d 只, 失败 %d 只, 用时 %.1f 分钟",
                len(all_done), len(all_failed), (time.time() - t0) / 60)


if __name__ == "__main__":
    main()
