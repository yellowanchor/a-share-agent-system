# -*- coding: utf-8 -*-
"""规则层历史信号生成（回测的"历史重放"底座）。

为什么要有这一层
----------------
每日推荐（`scripts/run_daily_signals.py`）走的是三 Agent + 本地大模型，一天 20 只就要 2–5 分钟，
无法回放三年。而回测要回答的是"这套四因子打分长期是否有效"，所以先用**纯 pandas 规则层**
把历史每一天的信号批量算出来，再交给撮合引擎；大模型层只做近 3 个月的样本外对照
（正好也是论文的消融实验）。

与线上的一致性
--------------
权重、阈值、公式全部与 `configs/scoring.yaml` + `run_daily_signals.py` 保持同一口径：
- 舆情 35%：`50 + 50 × 近 5 日净情感`（净情感 = (利多−利空) / 总条数）
- 趋势 25%：`50 + 20 日涨幅×1.5`（截断 ±25）+ 站上 MA20(+8) + MA5>MA20(+7)
- 量能 20%：量比 = 成交量 / 20 日均量，按"温和放量最好、天量反而扣分"给分
- 风险 20%：ATR 占比与年化波动越高扣分越多
总分 ≥ 65 记为 `buy`，45–65 记为 `watch`。

时间口径（防未来函数）
----------------------
每一行 `(date, code)` 只用 `date` 当日收盘及之前的数据；情感用发布日 ≤ `date` 的新闻。
信号在 `date` 收盘后生成，因此撮合只能发生在 **date 的下一交易日**。

用法
----
    python scripts/gen_backtest_signals.py --start 2023-01-01 --end 2026-11-23
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Dict, Optional

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "data" / "processed" / "market_data.duckdb"
SENT_PARQUET = ROOT / "data" / "processed" / "sentiment_labeled.parquet"
OUT_DIR = ROOT / "data" / "backtest"

# 选股池（与线上每日推荐同一口径）
POOL_TOP_LIQUID = 300      # 近 20 日成交额 Top300
POOL_TOP_MOVE = 100        # 当日涨幅绝对值 Top100
MIN_HISTORY = 60           # 至少 60 根 K 线才参与打分

# 权重与阈值（与 configs/scoring.yaml 一致）
from src.scoring import load_spec as _load_spec

SPEC = _load_spec()          # 权重与方向都来自 configs/scoring.yaml（回测与线上共用）
W_SENT, W_TREND, W_VOL, W_RISK = (SPEC["weights"][k] for k in
                                  ("sentiment", "trend", "volume", "risk"))
DIR = SPEC["direction"]
#: 因子键 → 分项列名（sentiment 的列叫 s_sent，不是 s_sentiment）
COLMAP = {"sentiment": "sent", "trend": "trend", "volume": "vol", "risk": "risk"}
BUY_MIN, WATCH_MIN = 65.0, 45.0
MAX_ATR_PCT, MAX_ANNUAL_VOL = 6.0, 0.60

# 新闻闸门的"规则层"部分：只做关键词硬否决（大模型语义闸门留给 LLM 层）
HARD_VETO_HINTS = (
    "退市", "立案调查", "财务造假", "商誉减值", "业绩预亏", "预亏",
    "清仓减持", "质押爆仓", "破产", "债务违约", "被诉讼", "实控人被",
)


def _six(code: str) -> str:
    """'sh.600519' → '600519'。"""
    return str(code).split(".")[-1]


# --------------------------------------------------------------------- 数据
def load_panel(
    db_path: Path = DEFAULT_DB,
    start: str = "2023-01-01",
    end: str = "2026-11-23",
    warmup_days: int = 180,
) -> pd.DataFrame:
    """读不复权日线（真实成交价），并向前多取一段用于算指标。"""
    warm_start = (pd.Timestamp(start) - pd.Timedelta(days=warmup_days)).strftime("%Y-%m-%d")
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        df = con.execute(
            """
            SELECT code, date, open, high, low, close, preclose, volume, amount, pct_chg
            FROM daily_raw
            WHERE date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
            ORDER BY code, date
            """,
            [warm_start, end],
        ).fetch_df()
    finally:
        con.close()
    df["code"] = df["code"].astype(str)
    df["date"] = pd.to_datetime(df["date"])
    return df


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """按股票分组计算均线 / ATR / 量比 / 动量 / 年化波动。"""
    df = df.sort_values(["code", "date"], kind="mergesort").reset_index(drop=True)
    g = df.groupby("code", sort=False)

    df["ma5"] = g["close"].transform(lambda s: s.rolling(5).mean())
    df["ma20"] = g["close"].transform(lambda s: s.rolling(20).mean())
    df["ma60"] = g["close"].transform(lambda s: s.rolling(60).mean())
    df["vol_ma20"] = g["volume"].transform(lambda s: s.rolling(20).mean())
    df["vol_ratio"] = df["volume"] / df["vol_ma20"]

    prev_close = g["close"].shift(1)
    tr = pd.concat(
        [
            (df["high"] - df["low"]).abs(),
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["tr"] = tr
    df["atr14"] = df.groupby("code", sort=False)["tr"].transform(
        lambda s: s.rolling(14).mean()
    )
    df["atr_pct"] = df["atr14"] / df["close"] * 100

    df["ret_20d"] = g["close"].transform(lambda s: s / s.shift(20) - 1) * 100

    # 年化波动：60 日日收益标准差 × √252（不复权口径，含除权跳空）
    # 说明：部分源（腾讯）的 preclose 为空，此时退回 close.pct_change()，否则整列 NaN
    ret_by_preclose = df["close"] / df["preclose"] - 1
    ret_by_close = g["close"].transform(lambda s: s.pct_change())
    df["daily_ret"] = ret_by_preclose.where(ret_by_preclose.notna(), ret_by_close)
    df["annual_vol"] = df.groupby("code", sort=False)["daily_ret"].transform(
        lambda s: s.rolling(60, min_periods=40).std() * math.sqrt(252)
    )
    return df


def load_sentiment(db_path: Path = SENT_PARQUET) -> pd.DataFrame:
    """把情感标签聚合成 [code6, date, n, pos, neg]（date = 新闻发布日）。"""
    if not Path(db_path).exists():
        return pd.DataFrame(columns=["code6", "date", "n", "pos", "neg"])
    raw = pd.read_parquet(db_path, columns=["stock_code", "publish_time", "label"])
    raw["code6"] = raw["stock_code"].astype(str).map(_six)
    raw["date"] = pd.to_datetime(raw["publish_time"]).dt.normalize()
    raw["pos"] = (raw["label"] == "positive").astype(int)
    raw["neg"] = (raw["label"] == "negative").astype(int)
    out = (
        raw.groupby(["code6", "date"], as_index=False)
        .agg(n=("label", "size"), pos=("pos", "sum"), neg=("neg", "sum"))
    )
    return out


def attach_sentiment(df: pd.DataFrame, sent: pd.DataFrame) -> pd.DataFrame:
    """把近 5 日情感接到每个交易日上（只用发布日 ≤ 当日的新闻）。"""
    if sent.empty:
        df["n5"] = 0
        df["net5"] = 0.0
        return df

    df["code6"] = df["code"].map(_six)
    merged = df.merge(sent, left_on=["code6", "date"], right_on=["code6", "date"], how="left")
    merged[["n", "pos", "neg"]] = merged[["n", "pos", "neg"]].fillna(0)

    g = merged.groupby("code6", sort=False)
    merged["n5"] = g["n"].transform(lambda s: s.rolling(5, min_periods=1).sum())
    merged["pos5"] = g["pos"].transform(lambda s: s.rolling(5, min_periods=1).sum())
    merged["neg5"] = g["neg"].transform(lambda s: s.rolling(5, min_periods=1).sum())
    denom = merged["n5"].replace(0, float("nan"))
    merged["net5"] = ((merged["pos5"] - merged["neg5"]) / denom).fillna(0.0)
    return merged


# --------------------------------------------------------------------- 打分
def score_frame(df: pd.DataFrame) -> pd.DataFrame:
    """四因子打分（向量化）。"""
    # 舆情：净情感 [-1,1] → [0,100]
    df["s_sent"] = (50 + 50 * df["net5"]).clip(0, 100)

    # 趋势：20 日动量 + 均线位置
    trend = 50 + (df["ret_20d"] * 1.5).clip(-25, 25)
    trend = trend + (df["close"] > df["ma20"]).astype(int) * 8 - (
        df["close"] < df["ma20"]
    ).astype(int) * 8
    trend = trend + (df["ma5"] > df["ma20"]).astype(int) * 7 - (
        df["ma5"] < df["ma20"]
    ).astype(int) * 7
    df["s_trend"] = trend.clip(0, 100)

    # 量能：量比 1 左右最好，天量扣分
    vr = df["vol_ratio"].fillna(1.0)
    low = 40 + (vr - 0.5) * 20
    high = 60 + ((vr - 1) * 40).clip(upper=30) - ((vr - 3).clip(lower=0) * 15)
    df["s_vol"] = pd.Series(
        [h if v > 1 else l for v, l, h in zip(vr, low, high)], index=df.index
    ).clip(0, 100)

    # 风险：ATR 占比 + 年化波动
    risk = 100 - ((df["atr_pct"] - MAX_ATR_PCT).clip(lower=0) * 6)
    risk = risk - ((df["annual_vol"] - MAX_ANNUAL_VOL).clip(lower=0) * 60)
    df["s_risk"] = risk.clip(0, 100)

    def _oriented(name: str):
        """按 direction 处理分项：-1 表示反向使用（100 − v）。"""
        col = df[f"s_{COLMAP[name]}"]
        return col if DIR.get(name, 1) > 0 else 100.0 - col

    df["score"] = (
        W_SENT * _oriented("sentiment")
        + W_TREND * _oriented("trend")
        + W_VOL * _oriented("volume")
        + W_RISK * _oriented("risk")
    ).round(2)
    return df


def build_pool(df: pd.DataFrame) -> pd.DataFrame:
    """每个交易日挑出候选池：成交额 Top300 ∪ 涨幅绝对值 Top100。"""
    d = df.copy()
    d["turnover"] = d["amount"].fillna(d["volume"] * (d["high"] + d["low"] + d["close"]) / 3.0)
    d["turnover_ma20"] = d.groupby("code", sort=False)["turnover"].transform(
        lambda s: s.rolling(20).mean()
    )

    liq = (
        d.dropna(subset=["turnover_ma20"])
        .sort_values(["date", "turnover_ma20"], ascending=[True, False])
        .groupby("date", sort=False)
        .head(POOL_TOP_LIQUID)
    )
    mov = (
        d.dropna(subset=["pct_chg"])
        .assign(abs_chg=lambda x: x["pct_chg"].abs())
        .sort_values(["date", "abs_chg"], ascending=[True, False])
        .groupby("date", sort=False)
        .head(POOL_TOP_MOVE)
    )
    pool = pd.concat([liq, mov]).drop_duplicates(subset=["date", "code"])
    return d.merge(pool[["date", "code"]], on=["date", "code"], how="inner")


def apply_gate(df: pd.DataFrame, sent: pd.DataFrame) -> pd.DataFrame:
    """规则层新闻闸门：命中硬否决关键词 → 直接剔除；强负面且证据足够 → 降级 watch。"""
    d = df.copy()
    d["veto"] = False

    if not sent.empty:
        # 用近 5 日"强负面条数"近似关键词扫描（离线场景拿不到逐条正文，交给 LLM 层补）
        d["veto"] = (d["n5"] >= 3) & (d["net5"] <= -0.60)
    d.loc[d["veto"], "score"] = (d.loc[d["veto"], "score"] * 0.6).round(2)

    d["verdict"] = "watch"
    d.loc[d["score"] >= BUY_MIN, "verdict"] = "buy"
    d.loc[(d["score"] < WATCH_MIN), "verdict"] = "reject"
    d.loc[d["veto"], "verdict"] = "reject"
    return d


# --------------------------------------------------------------------- 入口
def build_signals(
    start: str = "2023-01-01",
    end: str = "2026-11-23",
    db_path: Path = DEFAULT_DB,
    out_dir: Path = OUT_DIR,
    verbose: bool = True,
) -> pd.DataFrame:
    """生成历史信号并落盘 parquet。"""
    if verbose:
        print(f"[1/5] 读日线 {start} → {end}（含前置 warmup）...")
    panel = load_panel(db_path, start, end)

    if verbose:
        print(f"[2/5] 计算指标（{len(panel):,} 行 / {panel['code'].nunique():,} 只）...")
    panel = add_indicators(panel)

    if verbose:
        print("[3/5] 选股池：成交额 Top300 ∪ 涨幅 Top100 ...")
    pool = build_pool(panel)

    if verbose:
        print("[4/5] 接入情感（近 5 日净情感）...")
    sent = load_sentiment()
    pool = attach_sentiment(pool, sent)

    if verbose:
        print("[5/5] 打分 + 规则闸门 ...")
    pool = score_frame(pool)
    pool = apply_gate(pool, sent)

    # 只保留信号区间内的行（warmup 段只用来算指标）
    pool = pool[pool["date"] >= pd.Timestamp(start)].copy()
    # 剔除指标不全的（上市不足 60 日 / 停牌太久）
    need = ["ma20", "ma60", "atr14", "ret_20d", "annual_vol"]
    pool = pool.dropna(subset=need)
    pool = pool[pool["close"] > 0]

    cols = [
        "date", "code", "score", "verdict", "s_sent", "s_trend", "s_vol", "s_risk",
        "close", "atr_pct", "ret_20d", "vol_ratio", "net5", "n5", "veto",
    ]
    out = pool[cols].sort_values(["date", "score"], ascending=[True, False])

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "signals_rules.parquet"
    out.to_parquet(path, index=False)

    if verbose:
        days = out["date"].nunique()
        buys = out[out["verdict"] == "buy"]
        print(f"\n完成：{len(out):,} 行 / {days} 个交易日 / 覆盖 {out['code'].nunique():,} 只")
        print(f"  buy   {len(buys):,} 行，{days} 天平均 {len(buys)/max(days,1):.2f} 只/天")
        print(f"  watch {(out['verdict']=='watch').sum():,} 行 / reject {(out['verdict']=='reject').sum():,} 行")
        print(f"  分数分位 10/50/90: {out['score'].quantile([.1,.5,.9]).round(1).tolist()}")
        print(f"  有情感覆盖的行占比: {(out['n5']>0).mean():.1%}")
        print(f"  落盘: {path}")
    return out


if __name__ == "__main__":
    build_signals()
