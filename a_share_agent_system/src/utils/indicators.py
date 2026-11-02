# -*- coding: utf-8 -*-
"""indicators.py — 轻量技术指标与行情快照计算

设计说明
--------
不引入 TA-Lib（Windows 需编译、依赖 C 库），全部用 pandas 向量化实现，
保证在纯 Python 环境下可复现。指标口径与常见行情软件一致，便于论文中
对账与答辩说明。

用法:
    from src.utils.indicators import summarize_market, MarketSnapshot
    snap = summarize_market(df)          # df 为 daily_hfq 查询结果
    print(snap.close, snap.ma20, snap.rsi14)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

__all__ = [
    "sma",
    "ema",
    "rsi",
    "atr",
    "volume_ratio",
    "annualized_volatility",
    "support_resistance",
    "MarketSnapshot",
    "summarize_market",
    "snapshot_to_dict",
]


# ---------------------------------------------------------------------------
# 基础指标
# ---------------------------------------------------------------------------
def sma(series: pd.Series, n: int) -> float:
    """简单移动平均（末值）。样本不足时返回 NaN。"""
    if len(series) < n:
        return float("nan")
    return float(series.tail(n).mean())


def ema(series: pd.Series, n: int) -> float:
    """指数移动平均（末值，adjust=False 口径）。"""
    if len(series) < n:
        return float("nan")
    return float(series.ewm(span=n, adjust=False).mean().iloc[-1])


def rsi(close: pd.Series, n: int = 14) -> float:
    """Wilder RSI。

    计算: 先取一阶差分，分别用 Wilder 平滑（alpha=1/n）处理涨/跌幅度，
    再算 RS = 平均涨幅 / 平均跌幅，RSI = 100 - 100/(1+RS)。
    样本不足或跌幅为 0 时返回边界值。
    """
    if len(close) < n + 1:
        return float("nan")
    diff = close.diff().dropna()
    if len(diff) < n:
        return float("nan")
    gain = diff.clip(lower=0.0)
    loss = (-diff).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / n, adjust=False).mean().iloc[-1]
    avg_loss = loss.ewm(alpha=1.0 / n, adjust=False).mean().iloc[-1]
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100.0 - 100.0 / (1.0 + rs))


def atr(df: pd.DataFrame, n: int = 14) -> float:
    """平均真实波幅（Wilder 平滑）。需要 high / low / close 三列。"""
    if len(df) < n + 1 or not {"high", "low", "close"}.issubset(df.columns):
        return float("nan")
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    tr = tr.dropna()
    if len(tr) < n:
        return float("nan")
    return float(tr.ewm(alpha=1.0 / n, adjust=False).mean().iloc[-1])


def volume_ratio(df: pd.DataFrame, n: int = 5, base: int = 60) -> float:
    """量比: 近 n 日均量 / 前 base 日均量。衡量近期成交活跃度变化。"""
    if len(df) < n or "volume" not in df.columns:
        return float("nan")
    vol = df["volume"].astype(float)
    recent = vol.tail(n).mean()
    hist = vol.tail(base + n).head(base).mean() if len(vol) >= base + n else vol.mean()
    if not hist or math.isnan(hist) or hist == 0:
        return float("nan")
    return float(recent / hist)


def annualized_volatility(close: pd.Series, n: int = 20) -> float:
    """年化波动率: 近 n 日收益率标准差 × sqrt(244)（A股年交易日约 244 天）。"""
    if len(close) < n + 1:
        return float("nan")
    ret = close.pct_change().dropna().tail(n)
    if len(ret) < 2:
        return float("nan")
    return float(ret.std(ddof=1) * math.sqrt(244))


def support_resistance(df: pd.DataFrame, n: int = 60) -> Dict[str, float]:
    """近 n 日支撑/阻力位: 取区间最低收盘与最高收盘。"""
    if len(df) == 0:
        return {"support": float("nan"), "resistance": float("nan")}
    sub = df.tail(n)
    return {
        "support": float(sub["low"].min()),
        "resistance": float(sub["high"].max()),
    }


# ---------------------------------------------------------------------------
# 行情快照
# ---------------------------------------------------------------------------
@dataclass
class MarketSnapshot:
    """面向 Agent 的行情快照（纯标量，便于直接塞进提示词）。"""

    code: str = ""
    as_of: str = ""
    close: float = float("nan")
    ma5: float = float("nan")
    ma20: float = float("nan")
    ma60: float = float("nan")
    rsi14: float = float("nan")
    atr14: float = float("nan")
    atr_pct: float = float("nan")       # ATR / close，便于跨价位比较
    ret_5d: float = float("nan")        # 近 5 日涨跌幅（%）
    ret_20d: float = float("nan")       # 近 20 日涨跌幅（%）
    ret_60d: float = float("nan")       # 近 60 日涨跌幅（%）
    vol_ratio: float = float("nan")     # 5 日量比
    annual_vol: float = float("nan")    # 年化波动率
    support: float = float("nan")
    resistance: float = float("nan")
    n_bars: int = 0
    extra: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        """转为可 JSON 序列化的字典（NaN → None）。"""
        d = {k: _clean(v) for k, v in self.__dict__.items() if k != "extra"}
        d.update({k: _clean(v) for k, v in self.extra.items()})
        return d


def _clean(v: object) -> Optional[object]:
    """NaN/Inf → None，便于 JSON 序列化。"""
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, (np.floating,)):
        return _clean(float(v))
    if isinstance(v, (np.integer,)):
        return int(v)
    return v


def summarize_market(df: pd.DataFrame, code: str = "") -> MarketSnapshot:
    """由日线 DataFrame 计算行情快照。

    Args:
        df: daily_hfq / daily_raw 查询结果，需按日期升序，
            含 date/open/high/low/close/volume 列。
        code: 股票代码（写入快照便于溯源）。

    Returns:
        MarketSnapshot。df 为空时返回全 NaN 快照（n_bars=0）。
    """
    if df is None or len(df) == 0:
        return MarketSnapshot(code=code, n_bars=0)

    d = df.copy()
    if "date" in d.columns:
        d = d.sort_values("date")
    close = d["close"].astype(float)

    snap = MarketSnapshot()
    snap.code = code
    snap.n_bars = int(len(d))
    if "date" in d.columns:
        snap.as_of = str(pd.to_datetime(d["date"].iloc[-1]).date())
    snap.close = float(close.iloc[-1])
    snap.ma5 = sma(close, 5)
    snap.ma20 = sma(close, 20)
    snap.ma60 = sma(close, 60)
    snap.rsi14 = rsi(close, 14)
    a = atr(d, 14)
    snap.atr14 = a
    snap.atr_pct = (a / snap.close * 100.0) if (a == a and snap.close) else float("nan")
    snap.ret_5d = _pct(close, 5)
    snap.ret_20d = _pct(close, 20)
    snap.ret_60d = _pct(close, 60)
    snap.vol_ratio = volume_ratio(d)
    snap.annual_vol = annualized_volatility(close)
    sr = support_resistance(d)
    snap.support = sr["support"]
    snap.resistance = sr["resistance"]
    return snap


def _pct(close: pd.Series, n: int) -> float:
    """近 n 日涨跌幅（%），样本不足返回 NaN。"""
    if len(close) <= n:
        return float("nan")
    base = float(close.iloc[-n - 1])
    if base == 0 or math.isnan(base):
        return float("nan")
    return float((float(close.iloc[-1]) / base - 1.0) * 100.0)


def snapshot_to_dict(snap: MarketSnapshot) -> Dict[str, object]:
    """兼容函数: 快照转字典。"""
    return snap.to_dict()


def fmt_snapshot_lines(snap: MarketSnapshot) -> List[str]:
    """把快照格式化为提示词可用的多行文本（已处理 None/NaN）。"""
    d = snap.to_dict()
    labels = [
        ("as_of", "数据截止"), ("close", "最新收盘"), ("ma5", "MA5"),
        ("ma20", "MA20"), ("ma60", "MA60"), ("rsi14", "RSI14"),
        ("atr14", "ATR14"), ("atr_pct", "ATR占比%"), ("ret_5d", "近5日涨跌%"),
        ("ret_20d", "近20日涨跌%"), ("ret_60d", "近60日涨跌%"),
        ("vol_ratio", "5日量比"), ("annual_vol", "年化波动率"),
        ("support", "近60日支撑"), ("resistance", "近60日阻力"),
        ("n_bars", "样本K线数"),
    ]
    out: List[str] = []
    for key, cn in labels:
        v = d.get(key)
        if v is None:
            continue
        out.append(f"- {cn}: {v:.2f}" if isinstance(v, float) else f"- {cn}: {v}")
    return out
