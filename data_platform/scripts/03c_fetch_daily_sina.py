# -*- coding: utf-8 -*-
"""
脚本 03c：全A股日线主采（新浪财经快源）
======================================
新浪 hisdata_klc2 接口返回全量历史（上市至今），含退市股，
解密走常驻 Node 服务（scripts/sina_decode_server.js），
比 akshare 的 py_mini_racer 每次新建引擎快 ~100 倍。

字段对齐 BaoStock 主采版（03b）：
  date, code, open, high, low, close, preclose, volume(股), amount(元),
  turn(%), pct_chg(%), tradestatus, pe_ttm, pb_mrq, ps_ttm,
  pcf_ncf_ttm, is_st
  新浪源无估值字段 → pe_ttm 等置 NaN，is_st=0，tradestatus=1
  另加 source="sina" 与 hfq_factor 列供溯源

复权：
  hfq 后复权价 = 原始价 × 后复权因子(hfq.js，按除权日更新)
  raw 不复权原始价

分片序号从 2000 起（BaoStock 版用 1000 段），进度文件
progress/daily_done.json 与 03b 共用，实现无缝断点续传。

用法: python scripts/03c_fetch_daily_sina.py [--threads 4] [--limit N] [--delisted-only]
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import requests  # noqa: E402
from tqdm import tqdm  # noqa: E402

from src.storage import (  # noqa: E402
    ensure_dirs, RAW_DIR, save_shard, save_progress, load_progress,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BASE = Path(__file__).resolve().parent.parent
NODE = "C:/Users/djc/.workbuddy/binaries/node/versions/22.22.2/node.exe"

SHARD_SIZE = 100
SHARD_BASE = 2000  # 分片段：与 BaoStock 版(1000)错开

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://finance.sina.com.cn",
}
HIST_URL = "https://finance.sina.com.cn/realstock/company/{sym}/hisdata_klc2/klc_kl.js"
HFQ_URL = "https://finance.sina.com.cn/realstock/company/{sym}/hfq.js"
AMOUNT_URL = ("https://stock.finance.sina.com.cn/stock/api/jsonp.php/"
              "var%20KKE_ShareAmount_{sym}=/StockService.getAmountBySymbol?_=20&symbol={sym}")

# ---------------------------------------------------------------------------
# Node 解密服务（常驻进程，stdin/stdout 行协议）
# ---------------------------------------------------------------------------
_node_proc: subprocess.Popen | None = None
_node_lock = threading.Lock()


def start_node() -> None:
    """启动常驻 Node 解密服务"""
    global _node_proc
    if _node_proc is not None:
        return
    _node_proc = subprocess.Popen(
        [NODE, "scripts/sina_decode_server.js"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=str(BASE), text=True, encoding="utf-8", bufsize=1,
    )
    logger.info("Node 解密服务已启动 pid=%d", _node_proc.pid)


def stop_node() -> None:
    global _node_proc
    if _node_proc is not None:
        _node_proc.terminate()
        _node_proc = None


def decode_kline(enc: str, timeout: float = 20) -> list[dict]:
    """发送加密串到 Node 服务并等待解密结果（线程安全）"""
    req_id = abs(hash(enc)) % 10_000_000
    with _node_lock:
        _node_proc.stdin.write(json.dumps({"id": req_id, "enc": enc}) + "\n")
        _node_proc.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = _node_proc.stdout.readline()
            if not line:
                raise RuntimeError("Node 服务已退出")
            line = line.strip()
            if not line:
                continue
            try:
                resp = json.loads(line)
            except json.JSONDecodeError:
                continue
            if resp.get("id") == req_id:
                if resp.get("ok"):
                    return resp["data"]
                raise RuntimeError(resp.get("err", "解密失败"))
        raise TimeoutError("解密超时")


# ---------------------------------------------------------------------------
# 单只股票采集
# ---------------------------------------------------------------------------
def fetch_one(code: str, market: str, symbol: str,
              session: requests.Session, retry: int = 2) -> tuple[str, pd.DataFrame, pd.DataFrame]:
    """
    采集单只股票日线，返回 (code, hfq_df, raw_df)
    hfq_df: 后复权; raw_df: 不复权。失败抛异常由上层捕获。
    """
    sym = market + symbol
    last_err = None
    for attempt in range(retry + 1):
        try:
            # 1) 全量历史（加密）→ Node 解密
            r = session.get(HIST_URL.format(sym=sym), timeout=15)
            m = re.search(r'"(.*)"', r.text)
            if not m or r.status_code != 200:
                raise RuntimeError(f"hist 请求异常 st={r.status_code} len={len(r.text)}")
            raw_rows = decode_kline(m.group(1))
            if not raw_rows:
                raise RuntimeError("解密结果为空")
            df = pd.DataFrame(raw_rows)
            df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
            df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
            df = df.astype({"open": float, "high": float, "low": float,
                            "close": float, "volume": float, "amount": float})

            # 2) 后复权因子
            hist_dates = pd.to_datetime(df["date"]).sort_values()
            hfq_factor = pd.Series(1.0, index=df.index)
            try:
                r2 = session.get(HFQ_URL.format(sym=sym), timeout=10)
                if r2.status_code == 200 and "data" in r2.text:
                    body = r2.text.split("=", 1)[1].split("\n")[0].strip().rstrip(";")
                    fac = pd.DataFrame(json.loads(body)["data"])
                    if not fac.empty:
                        fac["d"] = pd.to_datetime(fac["d"])
                        fac["f"] = fac["f"].astype(float)
                        # 因子按日期排序后 reindex+ffill：每个交易日取最近一次 <= 该日的因子
                        factor_series = fac.set_index("d")["f"].sort_index()
                        hfq_factor = (factor_series.reindex(hist_dates, method="ffill")
                                      .fillna(1.0).reset_index(drop=True))
                        hfq_factor.index = df.index
            except Exception as e:  # noqa: BLE001
                logger.warning("%s 复权因子获取失败，按不复权处理: %s", code, e)
                hfq_factor = pd.Series(1.0, index=df.index)

            # 3) 流通股本 → 换手率（可选，失败则 NaN）
            turn = pd.Series(float("nan"), index=df.index)
            try:
                r3 = session.get(AMOUNT_URL.format(sym=sym), timeout=10)
                m3 = re.search(r"\((\[.*\])\)", r3.text, re.S)
                if m3:
                    amt = pd.DataFrame(json.loads(m3.group(1)))
                    amt.columns = ["date", "outstanding_share"]  # 单位: 万股
                    amt["date"] = pd.to_datetime(amt["date"]).dt.date
                    amt = amt.sort_values("date")
                    share = pd.merge_asof(
                        pd.DataFrame({"date": df["date"]}).sort_values("date"),
                        amt, on="date", direction="backward",
                    )["outstanding_share"].ffill()
                    turn = df["volume"] / (share * 10000) * 100
            except Exception as e:  # noqa: BLE001
                logger.debug("%s 换手率计算失败: %s", code, e)

            # 4) 组装规范 DataFrame（hfq 与 raw）
            out = pd.DataFrame({
                "date": df["date"],
                "code": code,
                "open": df["open"],
                "high": df["high"],
                "low": df["low"],
                "close": df["close"],
                "preclose": df.get("prevclose", pd.Series(float("nan"), index=df.index)),
                "volume": df["volume"],
                "amount": df["amount"],
                "turn": turn,
                "tradestatus": 1,
                "pct_chg": (df["close"] / df["prevclose"] - 1) * 100
                           if "prevclose" in df else float("nan"),
                "pe_ttm": float("nan"),
                "pb_mrq": float("nan"),
                "ps_ttm": float("nan"),
                "pcf_ncf_ttm": float("nan"),
                "is_st": 0,
                "source": "sina",
                "hfq_factor": hfq_factor,
            })
            raw_df = out.copy()
            hfq_df = out.copy()
            for c in ("open", "high", "low", "close", "preclose"):
                hfq_df[c] = (out[c] * hfq_factor).round(2)
            return code, hfq_df, raw_df
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"采集失败: {last_err}")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="新浪财经全A股日线主采")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delisted-only", action="store_true", help="仅采退市股")
    ap.add_argument("--sleep", type=float, default=0.15, help="请求间隔(秒)")
    args = ap.parse_args()

    ensure_dirs()
    start_node()
    try:
        stock_list = pd.read_parquet(RAW_DIR / "stock_list.parquet")
        if args.delisted_only:
            codes = stock_list[(stock_list["in_hist"]) & (~stock_list["in_now"])]
        else:
            codes = stock_list
        logger.info("待采股票: %d 只", len(codes))

        done = load_progress("daily_done")
        todo = codes[~codes["code"].isin(done)]
        if args.limit:
            todo = todo.head(args.limit)
        logger.info("断点续传: 已完成 %d 只, 剩余 %d 只", len(done), len(todo))
        if todo.empty:
            logger.info("全部完成")
            return

        # 复用 Session（连接复用加速）
        session = requests.Session()
        session.headers.update(HEADERS)

        buffers: dict[str, list[pd.DataFrame]] = {"hfq": [], "raw": []}
        failed: list[str] = []
        # 【修复 2026-08-20】断点续传时从已有分片最大序号+1 续号，避免覆盖旧分片
        existing = sorted(RAW_DIR.glob("daily_hfq_part*.parquet"))
        last_idx = max(
            (int(p.stem.split("part")[1]) for p in existing),
            default=SHARD_BASE - 1,
        )
        shard_idx = max(SHARD_BASE, last_idx + 1)
        done_set = set(done)
        consec_fail = 0  # 连续失败计数（防封IP保护）
        t0 = time.time()

        def work(row) -> tuple[str, pd.DataFrame, pd.DataFrame]:
            code, market, symbol = row["code"], row["market"], row["symbol"]
            return fetch_one(code, market, symbol, session)

        with ThreadPoolExecutor(max_workers=args.threads) as pool:
            futs = {pool.submit(work, row): row["code"] for _, row in todo.iterrows()}
            pbar = tqdm(total=len(futs), desc="新浪日线主采", unit="只")
            for i, fut in enumerate(as_completed(futs)):
                code = futs[fut]
                try:
                    _, hfq_df, raw_df = fut.result()
                    buffers["hfq"].append(hfq_df)
                    buffers["raw"].append(raw_df)
                    done_set.add(code)
                    consec_fail = 0
                except Exception as e:  # noqa: BLE001
                    failed.append(code)
                    consec_fail += 1
                    logger.error("%s 失败: %s", code, e)
                    if consec_fail >= 15:
                        logger.warning("连续失败 %d 次，疑似限流，暂停 120s...", consec_fail)
                        time.sleep(120)
                        consec_fail = 0
                pbar.update(1)
                if (i + 1) % SHARD_SIZE == 0:
                    for k in ("hfq", "raw"):
                        if buffers[k]:
                            save_shard(pd.concat(buffers[k], ignore_index=True),
                                       f"daily_{k}", shard_idx)
                            buffers[k] = []
                    save_progress("daily_done", done_set)
                    shard_idx += 1
                time.sleep(args.sleep)  # 限流保护
            pbar.close()

        for k in ("hfq", "raw"):
            if buffers[k]:
                save_shard(pd.concat(buffers[k], ignore_index=True),
                           f"daily_{k}", shard_idx)
        save_progress("daily_done", done_set)

        if failed:
            with open(RAW_DIR / "daily_failed.json", "w", encoding="utf-8") as f:
                json.dump(failed, f, ensure_ascii=False, indent=2)
            logger.warning("失败 %d 只 -> %s", len(failed), RAW_DIR / "daily_failed.json")
        logger.info("新浪主采完成: 成功 %d, 失败 %d, 用时 %.1f 分钟",
                    len(todo) - len(failed), len(failed), (time.time() - t0) / 60)
    finally:
        stop_node()


if __name__ == "__main__":
    main()
