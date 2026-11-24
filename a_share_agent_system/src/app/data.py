# -*- coding: utf-8 -*-
"""data.py — 前端看板数据访问层

统一封装四类数据的读取（全部只读，不触碰训练/推理代码）：
1. 批量评测结果   data/processed/batch_eval_*.json   （Phase 3.2 产物）
2. 前复权日线     DuckDB daily_hfq                    （K 线图）
3. 词典情感标签   data/processed/sentiment_labeled.parquet（情感趋势线）
4. 股票名称映射   DuckDB financials_yjbb              （code → name）

所有函数都挂 @st.cache_data：看板反复交互时避免重读 1755 万行库。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data" / "processed"
DB_PATH = DATA_DIR / "market_data.duckdb"
SENT_PARQUET = DATA_DIR / "sentiment_labeled.parquet"
SIGNAL_DIR = ROOT / "data" / "signals"
GLOSSARY_PATH = ROOT / "data" / "knowledge" / "glossary.json"
STATUS_PATH = ROOT / "data" / "status" / "last_run.json"


# ---------------------------------------------------------------- utilities
def exchange_prefix(code: str) -> str:
    """6 位纯数字代码 → baostock 代码（sh.600519 / sz.000001）。"""
    code = code.strip()[-6:]
    return ("sh." if code[0] in "69" else "sz.") + code


def pretty_code(code: str) -> str:
    """baostock 代码 → 6 位纯数字（喂给批量结果 / 新闻表匹配）。"""
    return code.strip().replace("sh.", "").replace("sz.", "")[-6:]


# ---------------------------------------------------------------- loaders
@st.cache_data(show_spinner=False)
def list_batch_files() -> list[dict[str, Any]]:
    """扫描 data/processed 下全部批量评测文件，按修改时间倒序。

    Returns:
        [{"path": str, "name": "batch_eval_1102_191448", "mtime": float}, ...]
    """
    items = []
    for p in DATA_DIR.glob("batch_eval_*.json"):
        items.append(
            {"path": str(p), "name": p.stem, "mtime": p.stat().st_mtime}
        )
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


@st.cache_data(show_spinner=False)
def load_batch(path: str) -> dict[str, Any]:
    """读取一份批量评测 JSON（meta / stats / results）。"""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@st.cache_data(show_spinner=False)
def stock_names() -> dict[str, str]:
    """财务表 code → 最新股票名（取每只股票最后一次出现的 name）。"""
    import duckdb

    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        rows = con.execute(
            """
            SELECT code, arg_max(name, pub_date) AS name
            FROM financials_yjbb WHERE name IS NOT NULL
            GROUP BY code
            """
        ).fetchall()
        return {c: n for c, n in rows}
    finally:
        con.close()


@st.cache_data(show_spinner=False)
def load_kline(code: str, as_of: str, n: int = 120) -> pd.DataFrame:
    """取某只股票 as_of 截止日前 n 根前复权日线（K 线图数据源）。"""
    import duckdb

    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        df = con.execute(
            """
            SELECT date, open, high, low, close, preclose, volume, amount,
                   pct_chg, turn
            FROM daily_hfq
            WHERE code = ? AND date <= ?
            ORDER BY date DESC
            LIMIT ?
            """,
            [exchange_prefix(code), as_of, int(n)],
        ).df()
    finally:
        con.close()
    df = df.iloc[::-1].reset_index(drop=True)  # 时间正序
    if "pct_chg" not in df or df["pct_chg"].isna().all():
        df["pct_chg"] = df["close"].pct_change() * 100
    return df


@st.cache_data(show_spinner=False)
def load_sentiment_daily(code: str, as_of: str | None = None) -> pd.DataFrame:
    """按日聚合该股票新闻的情感分布（词典打标，Phase 2 产物）。

    Args:
        code: 股票代码（6 位或带 sh./sz. 前缀）。
        as_of: 数据截止日 "YYYY-MM-DD"。传入时只统计该日（含）之前的新闻，
               避免回测/复盘时看到"未来才知道的消息"；None 表示不截断。

    Returns:
        DataFrame[date, n, positive, negative, neutral, net_score]
    """
    df = pd.read_parquet(
        SENT_PARQUET, columns=["stock_code", "publish_time", "label"]
    )
    df = df[df["stock_code"] == pretty_code(code)]
    if as_of:
        # 当天 23:59:59 的消息仍属"当日可见"，故上界取当日 23:59:59（与 MarketStore 同口径）
        cutoff = f"{str(as_of)[:10]} 23:59:59"
        df = df[df["publish_time"].astype(str) <= cutoff]
    if df.empty:
        return pd.DataFrame(
            columns=["date", "n", "positive", "negative", "neutral", "net_score"]
        )
    ts = pd.to_datetime(df["publish_time"])
    day = ts.dt.date
    agg = df.assign(date=day).groupby("date")["label"].value_counts().unstack(
        fill_value=0
    )
    for col in ("positive", "negative", "neutral"):
        if col not in agg.columns:
            agg[col] = 0
    agg = agg.reset_index().rename(columns={"index": "date"})
    agg["n"] = agg["positive"] + agg["negative"] + agg["neutral"]
    agg["net_score"] = (agg["positive"] - agg["negative"]) / agg["n"]
    return agg[
        ["date", "n", "positive", "negative", "neutral", "net_score"]
    ]


# ---------------------------------------------------------------- helpers
@st.cache_data(show_spinner=False)
def latest_trade_date() -> str:
    """库里最新交易日（自定义个股分析用它当 as_of）。"""
    import duckdb

    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        row = con.execute("SELECT max(date) FROM daily_raw").fetchall()
    finally:
        con.close()
    return str(row[0][0])[:10] if row and row[0][0] else ""


@st.cache_data(show_spinner=False)
def has_code(code: str) -> bool:
    """该代码在库里是否有行情（用于前端提前给出友好提示）。"""
    import duckdb

    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        row = con.execute(
            "SELECT count(*) FROM daily_raw WHERE code = ? LIMIT 1",
            [exchange_prefix(code)],
        ).fetchall()
    finally:
        con.close()
    return bool(row and row[0][0])


def name_of(code: str, batch_meta: dict | None = None) -> str:
    """优先用评测批次的名称，回退财务表。"""
    code = pretty_code(code)
    if batch_meta and code in (batch_meta.get("names") or {}):
        return batch_meta["names"][code]
    return stock_names().get(code, "")


def fmt_num(v: Any, digits: int = 2, default: str = "—") -> str:
    """数字安全格式化；None/NaN → '—'。"""
    if v is None:
        return default
    try:
        f = float(v)
        if pd.isna(f):
            return default
        return f"{f:,.{digits}f}"
    except (TypeError, ValueError):
        return str(v)


# ---------------------------------------------------------------- 新增：每日建议 / 词条 / 运维
def list_signals() -> list[dict[str, Any]]:
    """data/signals 下全部推荐日文件，按日期（文件名）倒序。"""
    items = []
    for p in SIGNAL_DIR.glob("*.json"):
        items.append({"path": str(p), "name": p.stem, "mtime": p.stat().st_mtime})
    items.sort(key=lambda x: (x["name"], x["mtime"]), reverse=True)
    return items


@st.cache_data(show_spinner=False)
def load_signal(path: str) -> dict[str, Any]:
    """读取一份每日推荐 JSON（as_of / summary / items）。"""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def latest_signal() -> tuple[dict[str, Any] | None, str]:
    """最新一份推荐；无文件时返回 (None, '')。"""
    items = list_signals()
    if not items:
        return None, ""
    return load_signal(items[0]["path"]), items[0]["name"]


@st.cache_data(show_spinner=False)
def glossary() -> dict[str, Any]:
    """股市入门词条库（49 条，6 类）。"""
    if not GLOSSARY_PATH.exists():
        return {"entries": []}
    with open(GLOSSARY_PATH, encoding="utf-8") as f:
        return json.load(f)


@st.cache_data(show_spinner=False, ttl=300)
def db_stats() -> dict[str, Any]:
    """行情库体检：行数、最新交易日、新闻条数。"""
    import duckdb

    try:
        con = duckdb.connect(str(DB_PATH), read_only=True)
    except Exception:
        return {"ok": False}
    try:
        rows = con.execute("SELECT count(*) FROM daily_raw").fetchall()[0][0]
        last = con.execute("SELECT max(date) FROM daily_raw").fetchall()[0][0]
        news = con.execute("SELECT count(*) FROM news_articles").fetchall()[0][0]
        return {"ok": True, "rows": int(rows), "last_date": str(last), "news": int(news)}
    except Exception:
        return {"ok": False}
    finally:
        con.close()


def last_run() -> dict[str, Any]:
    """上一次 update_daily.py 的运行记录。"""
    if not STATUS_PATH.exists():
        return {}
    try:
        with open(STATUS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def clear_caches() -> None:
    """数据更新后清掉 st.cache_data，让看板读到新数据。"""
    st.cache_data.clear()


@st.cache_data(show_spinner=False, ttl=120)
def price_map(codes: tuple[str, ...]) -> dict[str, tuple[float | None, str]]:
    """批量取多只股票的最新**不复权**收盘价：{6位代码: (价, 日期)}。"""
    import duckdb

    six = [pretty_code(c) for c in codes]
    if not six:
        return {}
    full = [exchange_prefix(c) for c in six]
    placeholders = ",".join("?" for _ in full)
    try:
        con = duckdb.connect(str(DB_PATH), read_only=True)
    except Exception:
        return {}
    try:
        rows = con.execute(
            f"""
            SELECT code, close, date FROM (
                SELECT code, close, date,
                       row_number() OVER (PARTITION BY code ORDER BY date DESC) AS rn
                FROM daily_raw WHERE code IN ({placeholders})
            ) WHERE rn = 1
            """,
            full,
        ).fetchall()
    except Exception:
        return {}
    finally:
        con.close()
    return {pretty_code(c): (float(px), str(d)) for c, px, d in rows}
