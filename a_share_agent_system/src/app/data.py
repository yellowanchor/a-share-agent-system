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
def load_sentiment_daily(code: str) -> pd.DataFrame:
    """按日聚合该股票新闻的情感分布（词典打标，Phase 2 产物）。

    Returns:
        DataFrame[date, n, positive, negative, neutral, net_score]
    """
    df = pd.read_parquet(
        SENT_PARQUET, columns=["stock_code", "publish_time", "label"]
    )
    df = df[df["stock_code"] == pretty_code(code)]
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
