# -*- coding: utf-8 -*-
"""回测指标：只从「资金曲线 + 成交明细」出发，不碰策略逻辑。

为什么单独一个文件
------------------
论文里要报的指标（胜率、盈亏比、年化、最大回撤、Sharpe、Calmar）与实盘/模拟盘复盘
用的是**同一套定义**，所以算法必须只有一份实现。改口径只改这里。
"""

from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def _drawdown(equity: pd.Series) -> pd.Series:
    peak = equity.cummax()
    return equity / peak - 1.0


def compute_metrics(equity: pd.Series, trades: pd.DataFrame) -> Dict[str, float]:
    """计算全部指标。

    Args:
        equity: 逐交易日资金曲线（index=交易日）。
        trades: 成交明细，需含 pnl / ret / holding_days / entry_price / shares。

    Returns:
        指标字典；空数据时返回全 0，避免报告里出现 NaN。
    """
    out: Dict[str, float] = {}
    if equity is None or len(equity) < 2:
        return {k: 0.0 for k in (
            "initial", "final", "total_return", "cagr", "max_drawdown",
            "volatility", "sharpe", "calmar", "n_trades", "win_rate",
            "avg_win", "avg_loss", "profit_factor", "avg_holding_days",
            "avg_position_value", "exposure",
        )}

    equity = equity.astype(float)
    initial, final = float(equity.iloc[0]), float(equity.iloc[-1])
    days = max((equity.index[-1] - equity.index[0]).days, 1)
    years = days / 365.25

    rets = equity.pct_change().dropna()
    vol = float(rets.std() * np.sqrt(TRADING_DAYS)) if len(rets) > 1 else 0.0
    ann_ret = (final / initial) ** (1 / years) - 1 if years > 0 and initial > 0 else 0.0
    dd = _drawdown(equity)
    max_dd = float(dd.min())

    out["initial"] = initial
    out["final"] = final
    out["total_return"] = final / initial - 1 if initial else 0.0
    out["cagr"] = ann_ret
    out["max_drawdown"] = max_dd
    out["volatility"] = vol
    out["sharpe"] = (float(rets.mean() * TRADING_DAYS) / vol) if vol > 1e-12 else 0.0
    out["calmar"] = (ann_ret / abs(max_dd)) if max_dd < -1e-9 else 0.0

    if trades is None or len(trades) == 0:
        out.update({
            "n_trades": 0.0, "win_rate": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
            "profit_factor": 0.0, "avg_holding_days": 0.0, "avg_position_value": 0.0,
            "exposure": 0.0,
        })
        return out

    t = trades.copy()
    wins = t[t["pnl"] > 0]
    losses = t[t["pnl"] <= 0]
    gross_win = float(wins["pnl"].sum())
    gross_loss = float(-losses["pnl"].sum())

    out["n_trades"] = float(len(t))
    out["win_rate"] = len(wins) / len(t)
    out["avg_win"] = float(wins["ret"].mean()) if len(wins) else 0.0
    out["avg_loss"] = float(losses["ret"].mean()) if len(losses) else 0.0
    out["profit_factor"] = (gross_win / gross_loss) if gross_loss > 1e-9 else float("inf") if gross_win > 0 else 0.0
    out["avg_holding_days"] = float(t["holding_days"].mean())
    out["avg_position_value"] = float((t["entry_price"] * t["shares"]).mean())
    out["exposure"] = float(
        (t["entry_price"] * t["shares"]).mean() * len(t) / (initial * max(len(equity), 1))
    )
    return out


def equity_frame(result) -> pd.DataFrame:
    """把 BacktestResult 转成「date/equity/drawdown」三列，用于画图与报告附表。"""
    eq = result.equity.astype(float)
    df = pd.DataFrame({"date": eq.index, "equity": eq.values})
    df["drawdown"] = (eq / eq.cummax() - 1.0).values
    return df
