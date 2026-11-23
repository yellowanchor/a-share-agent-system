# -*- coding: utf-8 -*-
"""incremental.py — DuckDB 增量写入（日更流水线专用）
====================================================
``market_store.py`` 是**只读**查询层；这里补上写侧：

* ``upsert_daily``：行情按 ``(code, date)`` 反连接去重后插入，天然幂等；
* ``upsert_news``：新闻按规范化 URL 去重后插入，重复跑不会产生脏数据；
* ``last_bar_map``：一次扫描拿到每只股票"最后一行"，供后复权因子顺延。

设计取舍：**不建主键**。DuckDB 的 ``PRIMARY KEY`` 需要重建表，1.5 GB 库重写
代价高；用 ``WHERE NOT EXISTS`` 反连接达到同样的幂等效果，且可随时回退。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Optional

import duckdb
import pandas as pd

from .market_store import DEFAULT_DB
from src.data.collect.news_fetch import news_id, normalize_url

logger = logging.getLogger(__name__)

# 与 scripts/build_market_db.py 建表顺序保持一致
DAILY_COLS = [
    "date", "code", "open", "high", "low", "close", "preclose", "volume",
    "amount", "turn", "tradestatus", "pct_chg", "pe_ttm", "pb_mrq",
    "ps_ttm", "pcf_ncf_ttm", "is_st", "source", "hfq_factor",
]
NEWS_COLS = ["title", "text", "stock_code", "source", "publish_time", "url"]


def open_rw(db_path: Optional[Path] = None) -> duckdb.DuckDBPyConnection:
    """打开读写连接（DuckDB 单写者，勿与前端只读连接并发写）。"""
    path = Path(db_path) if db_path else DEFAULT_DB
    if not path.exists():
        raise FileNotFoundError(f"DuckDB 库不存在: {path}")
    con = duckdb.connect(str(path))
    con.execute("SET memory_limit='16GB'")
    con.execute("SET threads=8")
    return con


def latest_trade_date(con: duckdb.DuckDBPyConnection) -> Optional[str]:
    """库内最新交易日（``YYYY-MM-DD``）。"""
    row = con.execute("SELECT max(date) FROM daily_raw").fetchall()[0]
    return str(row[0]) if row and row[0] else None


def last_bar_map(con: duckdb.DuckDBPyConnection) -> dict[str, tuple]:
    """每只股票最后一行 ``(date, close, hfq_factor)``，用于因子顺延。"""
    rows = con.execute("""
        SELECT code, date, close, hfq_factor FROM (
            SELECT code, date, close, hfq_factor,
                   row_number() OVER (PARTITION BY code ORDER BY date DESC) AS rn
            FROM daily_raw
        ) WHERE rn = 1
    """).fetchall()
    return {r[0]: (str(r[1]), r[2], r[3]) for r in rows}


def upsert_daily(
    con: duckdb.DuckDBPyConnection,
    raw_rows: Iterable[dict],
    hfq_rows: Iterable[dict],
) -> int:
    """行情增量写入（先 raw 后 hfq，均按 (code, date) 去重）。

    Returns:
        实际新插入的 raw 行数。
    """
    raw_df = pd.DataFrame(list(raw_rows), columns=DAILY_COLS)
    hfq_df = pd.DataFrame(list(hfq_rows), columns=DAILY_COLS)
    if raw_df.empty:
        return 0

    raw_df["date"] = pd.to_datetime(raw_df["date"]).dt.date
    hfq_df["date"] = pd.to_datetime(hfq_df["date"]).dt.date

    con.register("stg_raw", raw_df)
    con.register("stg_hfq", hfq_df)
    try:
        before = con.execute("SELECT count(*) FROM daily_raw").fetchall()[0][0]
        con.execute(f"""
            INSERT INTO daily_raw ({", ".join(DAILY_COLS)})
            SELECT {", ".join(DAILY_COLS)} FROM stg_raw s
            WHERE NOT EXISTS (
                SELECT 1 FROM daily_raw d WHERE d.code = s.code AND d.date = s.date
            )
        """)
        after = con.execute("SELECT count(*) FROM daily_raw").fetchall()[0][0]
        con.execute(f"""
            INSERT INTO daily_hfq ({", ".join(DAILY_COLS)})
            SELECT {", ".join(DAILY_COLS)} FROM stg_hfq s
            WHERE NOT EXISTS (
                SELECT 1 FROM daily_hfq d WHERE d.code = s.code AND d.date = s.date
            )
        """)
        return int(after - before)
    finally:
        con.unregister("stg_raw")
        con.unregister("stg_hfq")


def existing_news_ids(con: duckdb.DuckDBPyConnection) -> set[str]:
    """库内已有新闻的稳定 id 集合（对历史 URL 重新规范化后计算）。"""
    urls = con.execute("SELECT url FROM news_articles").fetchall()
    return {news_id(u[0]) for u in urls if u and u[0]}


def upsert_news(
    con: duckdb.DuckDBPyConnection,
    rows: Iterable[dict],
    known_ids: Optional[set[str]] = None,
) -> list[dict]:
    """新闻增量写入（按规范化 URL 的稳定 id 去重）。

    Args:
        rows: ``fetch_news`` 产出的字典列表（含 ``id``）。
        known_ids: 已存在 id 集合；传入可跨批次复用，避免每批重扫全表。

    Returns:
        **真正新入库**的行（字典列表），便于调用方同步向量索引。
    """
    rows = list(rows)
    if not rows:
        return []
    if known_ids is None:
        known_ids = existing_news_ids(con)

    fresh = []
    for r in rows:
        rid = r.get("id") or news_id(r.get("url", ""))
        if not rid or rid in known_ids or not r.get("url"):
            continue
        known_ids.add(rid)
        fresh.append({
            "title": r.get("title"),
            "text": r.get("text"),
            "stock_code": r.get("stock_code"),
            "source": r.get("source", "eastmoney"),
            "publish_time": r.get("publish_time"),
            "url": normalize_url(r.get("url", "")),
        })

    if not fresh:
        return []
    df = pd.DataFrame(fresh, columns=NEWS_COLS)
    con.register("stg_news", df)
    try:
        con.execute(f"""
            INSERT INTO news_articles ({", ".join(NEWS_COLS)})
            SELECT {", ".join(NEWS_COLS)} FROM stg_news
        """)
        return fresh
    finally:
        con.unregister("stg_news")


def checkpoint(con: duckdb.DuckDBPyConnection) -> None:
    """把 WAL 刷进主文件（DuckDB 无 VACUUM，用 CHECKPOINT 收敛体积）。"""
    try:
        con.execute("CHECKPOINT")
    except Exception as e:  # noqa: BLE001
        logger.debug("CHECKPOINT 跳过: %s", e)


def table_sizes(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """各表行数，用于日志与体积盯梢。"""
    out = {}
    for t in ("daily_raw", "daily_hfq", "financials_yjbb", "news_articles"):
        try:
            out[t] = con.execute(f"SELECT count(*) FROM {t}").fetchall()[0][0]
        except Exception:  # noqa: BLE001
            out[t] = -1
    return out
