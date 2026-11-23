# -*- coding: utf-8 -*-
"""update_daily.py — 每日增量更新（行情 / 新闻 / 索引）
=====================================================
替代"删表全量重灌"的 ``build_market_db.py``：所有步骤都幂等，重跑无副作用。

用法::

    # 只更新行情（从库内最新交易日自动续抓）
    python scripts/update_daily.py --market

    # 只更新新闻（按股票池抓，默认 300 流动性 + 100 异动）
    python scripts/update_daily.py --news

    # 一起跑，顺带把新新闻增量写入向量库
    python scripts/update_daily.py --all --rag

    # 调试：只处理 5 只、不写库
    python scripts/update_daily.py --market --limit 5 --dry-run

    # 指定日期回补（YYYY-MM-DD）
    python scripts/update_daily.py --market --start 2026-08-21 --end 2026-11-21
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from concurrent.futures import ThreadPoolExecutor, as_completed  # noqa: E402

from src.data.collect.market_fetch import (  # noqa: E402
    SourceBlocked, fetch_raw_bars, reset_streak, set_pace, to_db_rows,
)
from src.data.collect.news_fetch import fetch_many  # noqa: E402
from src.data.collect.pool import build_daily_pool  # noqa: E402
from src.data.storage.incremental import (  # noqa: E402
    checkpoint, existing_news_ids, last_bar_map, latest_trade_date,
    open_rw, table_sizes, upsert_daily, upsert_news,
)

ROOT = Path(__file__).resolve().parent.parent
STATUS_DIR = ROOT / "data" / "status"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("update_daily")


# ----------------------------------------------------------------------
#  行情
# ----------------------------------------------------------------------
def update_market(
    con,
    start: Optional[str] = None,
    end: Optional[str] = None,
    codes: Optional[list[str]] = None,
    threads: int = 8,
    limit: int = 0,
    dry_run: bool = False,
    source: str = "auto",
) -> dict:
    """增量更新日线（不复权 + 后复权两张表）。

    Returns:
        ``{"codes": 处理只数, "inserted": 新插入行数, "failed": 失败只数,
        "last_date_before": 更新前最新日, "last_date_after": 更新后最新日}``
    """
    t0 = time.time()
    last_map = last_bar_map(con)
    before = latest_trade_date(con)
    end_day = (end or date.today().isoformat()).replace("-", "")

    targets = []
    for code, (last_day, close, factor) in last_map.items():
        if codes and code.split(".")[-1] not in codes and code not in codes:
            continue
        # 起点含库中最后一日：腾讯前复权序列要靠"上一根"才能反推首根新 bar 的前收盘
        beg = start.replace("-", "") if start else last_day.replace("-", "")
        if int(beg) > int(end_day):
            continue
        targets.append((code, beg, close, factor, last_day))

    if limit:
        targets = targets[:limit]
    logger.info("行情增量: %d 只待更新，区间 %s → %s，前复权因子顺延", len(targets), "各自最后一日+1", end_day)
    if not targets:
        return {"codes": 0, "inserted": 0, "failed": 0,
                "last_date_before": before, "last_date_after": before}

    buf_raw, buf_hfq, failed = [], [], []
    inserted = 0
    done = 0

    def flush():
        nonlocal inserted
        if dry_run or not buf_raw:
            return
        inserted += upsert_daily(con, buf_raw, buf_hfq)
        buf_raw.clear()
        buf_hfq.clear()

    with ThreadPoolExecutor(max_workers=max(1, threads)) as pool:
        futs = {pool.submit(_fetch_one, code, beg, end_day, close, factor, last_day, source): code
                for code, beg, close, factor, last_day in targets}
        for fut in as_completed(futs):
            code = futs[fut]
            done += 1
            try:
                raw_rows, hfq_rows = fut.result()
                buf_raw.extend(raw_rows)
                buf_hfq.extend(hfq_rows)
            except SourceBlocked:
                flush()
                raise
            except Exception as e:  # noqa: BLE001
                failed.append(code)
                logger.warning("行情失败 %s: %s", code, str(e)[:100])
            if done % 300 == 0:
                flush()
                logger.info("行情进度 %d/%d，新增 %d 行，失败 %d，用时 %.0fs",
                            done, len(targets), inserted, len(failed), time.time() - t0)
    flush()
    if not dry_run:
        checkpoint(con)

    after = latest_trade_date(con)
    logger.info("行情完成: 新增 %d 行，最新交易日 %s（原 %s），失败 %d，用时 %.0fs",
                inserted, after, before, len(failed), time.time() - t0)
    return {"codes": done, "inserted": inserted, "failed": len(failed),
            "last_date_before": before, "last_date_after": after}


def _fetch_one(code: str, beg: str, end: str, close, factor, last_day: str = None,
               source: str = "auto"):
    """单只股票：抓取 → 组装两张表的行（丢弃库内已有的锚点行）。"""
    bars = fetch_raw_bars(code, beg, end, source=source)
    if not bars:
        return [], []
    if last_day:
        bars = [b for b in bars if str(b.get("date", ""))[:10] > str(last_day)[:10]]
    if not bars:
        return [], []
    return to_db_rows(code, bars, close, factor)


# ----------------------------------------------------------------------
#  新闻
# ----------------------------------------------------------------------
def update_news(
    con,
    end: Optional[str] = None,
    codes: Optional[list[str]] = None,
    top_turnover: int = 300,
    top_move: int = 100,
    max_pages: int = 1,
    threads: int = 8,
    limit: int = 0,
    dry_run: bool = False,
    with_rag: bool = False,
) -> dict:
    """按股票池抓新闻并增量入库（可顺带更新向量索引）。"""
    t0 = time.time()
    as_of = end or date.today().isoformat()

    if codes:
        pool_codes = [c.split(".")[-1] for c in codes]
    else:
        pool_codes = build_daily_pool(
            con, top_turnover=top_turnover, top_move=top_move,
            extra_codes=[c.split(".")[-1] for c in (codes or [])],
        )
    if limit:
        pool_codes = pool_codes[:limit]
    logger.info("新闻采集: 池 %d 只，每股 %d 页，as_of=%s", len(pool_codes), max_pages, as_of)

    known = existing_news_ids(con)
    collected: list[dict] = []
    inserted = 0

    def on_batch(rows: list[dict]) -> None:
        nonlocal inserted
        if dry_run:
            collected.extend(rows)
            return
        fresh = upsert_news(con, rows, known)
        inserted += len(fresh)
        collected.extend(fresh)  # 只把真正新增的交给向量库

    fetch_many(pool_codes, max_pages=max_pages, threads=threads,
               as_of=as_of, on_batch=on_batch)
    if not dry_run:
        checkpoint(con)

    rag_n = 0
    if with_rag and not dry_run:
        rag_n = _update_rag(collected)

    logger.info("新闻完成: 新增入库 %d 条，RAG 增量 %d 条，用时 %.0fs",
                inserted, rag_n, time.time() - t0)
    return {"pool": len(pool_codes), "inserted": inserted, "rag": rag_n}


def _update_rag(rows: list[dict]) -> int:
    """把新新闻写入向量库（增量、幂等）；失败只告警不中断流水线。"""
    if not rows:
        return 0
    try:
        from src.models.rag.indexer import incremental_rag_update
        n = incremental_rag_update(rows)
        logger.info("RAG 增量完成: %d 条", n)
        return int(n)
    except Exception as e:  # noqa: BLE001
        logger.warning("RAG 增量失败（不影响行情/新闻入库）: %s", str(e)[:200])
        return 0


# ----------------------------------------------------------------------
#  CLI
# ----------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="每日增量更新（行情/新闻/向量索引）")
    ap.add_argument("--market", action="store_true", help="更新日线行情")
    ap.add_argument("--news", action="store_true", help="更新新闻")
    ap.add_argument("--all", action="store_true", help="行情+新闻")
    ap.add_argument("--rag", action="store_true", help="新闻入库后增量更新向量索引")
    ap.add_argument("--start", default=None, help="行情起始日 YYYY-MM-DD（缺省=库内最新日+1）")
    ap.add_argument("--end", default=None, help="结束日 YYYY-MM-DD（缺省=今天）")
    ap.add_argument("--codes", default=None, help="只处理指定 6 位代码，逗号分隔")
    ap.add_argument("--threads", type=int, default=4,
                    help="并发数（实测 >6 容易被数据源限流，默认 4）")
    ap.add_argument("--pace", type=float, default=0.15,
                    help="全局请求最小间隔秒数（默认 0.15；被限流时调到 0.5 以上）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 只（调试）")
    ap.add_argument("--source", default="auto", choices=["auto", "tencent", "sina", "em"],
                    help="行情数据源：auto=腾讯→新浪→东财；某源被限流时可强制指定备源")
    ap.add_argument("--max-pages", type=int, default=1, help="新闻每只翻页数")
    ap.add_argument("--pool-turnover", type=int, default=300, help="新闻池：近20日成交额TopN")
    ap.add_argument("--pool-move", type=int, default=100, help="新闻池：当日涨跌幅TopM")
    ap.add_argument("--db", default=None, help="DuckDB 路径（缺省 data/processed/market_data.duckdb）")
    ap.add_argument("--dry-run", action="store_true", help="只抓不写")
    args = ap.parse_args()

    do_market = args.market or args.all
    do_news = args.news or args.all
    if not (do_market or do_news):
        ap.error("至少指定 --market / --news / --all 之一")

    codes = [c.strip() for c in args.codes.split(",")] if args.codes else None
    set_pace(args.pace)
    reset_streak()
    con = open_rw(Path(args.db) if args.db else None)
    started = time.time()
    report: dict = {"started_at": datetime.now().isoformat(timespec="seconds")}

    try:
        logger.info("更新前: %s", table_sizes(con))
        if do_market:
            report["market"] = update_market(
                con, start=args.start, end=args.end, codes=codes,
                threads=args.threads, limit=args.limit, dry_run=args.dry_run,
                source=args.source,
            )
        if do_news:
            report["news"] = update_news(
                con, end=args.end, codes=codes,
                top_turnover=args.pool_turnover, top_move=args.pool_move,
                max_pages=args.max_pages, threads=args.threads,
                limit=args.limit, dry_run=args.dry_run, with_rag=args.rag,
            )
        logger.info("更新后: %s", table_sizes(con))
        report["ok"] = True
    except Exception as e:  # noqa: BLE001
        report["ok"] = False
        report["error"] = f"{type(e).__name__}: {e}"
        logger.exception("更新失败")
        raise
    finally:
        report["elapsed_sec"] = round(time.time() - started, 1)
        STATUS_DIR.mkdir(parents=True, exist_ok=True)
        (STATUS_DIR / "last_run.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        con.close()


if __name__ == "__main__":
    main()
