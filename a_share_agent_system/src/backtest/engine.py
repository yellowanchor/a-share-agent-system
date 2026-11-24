# -*- coding: utf-8 -*-
"""撮合引擎：把「每个交易日收盘后生成的信号」按 A 股约束回放成资金曲线。

设计取舍（都是为了"不要自己骗自己"）
------------------------------------
1. **T+1 开盘成交**：信号是收盘后生成的，不可能用当日收盘价成交。
2. **涨跌停不可成交**：开盘价已封涨停（≥ +9.8%）买不到，跌停（≤ −9.8%）卖不掉，顺延。
3. **停牌跳过**：当日没有行情行就不动作。
4. **成本按小资金估**：佣金万 2.5（最低 5 元）+ 卖出印花税 0.05% + 双边滑点 0.1%。
5. **三出口**：止损 / 止盈 / 持有期满，先触发先走；同日都触发时止损优先（保守）。
6. **固定仓位**：每笔按初始资金 × `position_pct` 下单，整手（100 股）向下取整。

本模块只做"一笔一笔算"，指标在 `metrics.py`，报告在 `scripts/run_backtest.py`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

import pandas as pd


@dataclass
class BacktestConfig:
    """回测参数（默认值 = 方案《表 3 回测引擎规格》里的保守口径）。"""

    initial_cash: float = 250_000.0     # 与模拟账户一致
    position_pct: float = 0.20          # 单票占初始资金比例
    max_positions: int = 5              # 最多同时持有 5 只
    top_n_per_day: int = 5              # 每日按分数取前 N 个信号
    hold_days: int = 20                 # 持有期满强制离场（交易日）
    stop_pct: float = -0.08             # signal 未给价时的止损（−8%）
    take_pct: float = 0.15              # signal 未给价时的止盈（+15%）
    commission: float = 0.00025         # 佣金万 2.5
    min_commission: float = 5.0         # 单笔最低 5 元
    stamp_tax: float = 0.0005           # 卖出印花税 0.05%
    slippage: float = 0.001             # 单边滑点 0.1%
    limit_pct: float = 0.098            # 涨跌停判定阈值（主板 10%，留 0.2% 余量）
    # 宇宙级过滤：不买仙股/ST（这两类在退市整理期会连续跌停，回测里是纯伤害）
    min_price: float = 2.0
    exclude_st: bool = True
    # ATR 自适应止损/止盈：信号里带 atr_pct 时按波动率定距离，避免固定 8% 被日常波动打掉
    use_atr_stops: bool = True
    atr_stop_mult: float = 2.0
    atr_take_mult: float = 3.0
    atr_min_stop: float = 0.05
    atr_max_stop: float = 0.15
    atr_max_take: float = 0.30


@dataclass
class Position:
    code: str
    shares: int
    entry_price: float
    entry_date: pd.Timestamp
    stop: float
    take: float
    hold_days: int = 0
    fee_in: float = 0.0


@dataclass
class Trade:
    code: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float
    shares: int
    reason: str
    pnl: float
    ret: float
    holding_days: int


@dataclass
class BacktestResult:
    equity: pd.Series
    trades: List[Trade] = field(default_factory=list)
    cash_final: float = 0.0
    positions_open: int = 0

    @property
    def trades_df(self) -> pd.DataFrame:
        if not self.trades:
            return pd.DataFrame(
                columns=[
                    "code", "entry_date", "exit_date", "entry_price", "exit_price",
                    "shares", "reason", "pnl", "ret", "holding_days",
                ]
            )
        return pd.DataFrame([t.__dict__ for t in self.trades])


def _commission(amount: float, cfg: BacktestConfig) -> float:
    return max(cfg.min_commission, amount * cfg.commission)


def prepare_bars(bars: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """把长表拆成 {code: DataFrame(index=date)}，查询 O(1)。"""
    out: Dict[str, pd.DataFrame] = {}
    for code, g in bars.groupby("code", sort=False):
        out[str(code)] = g.set_index("date").sort_index()
    return out


def run_backtest(
    signals: pd.DataFrame,
    bars: pd.DataFrame,
    cfg: Optional[BacktestConfig] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> BacktestResult:
    """执行回测。

    Args:
        signals: 至少包含 date / code / score / verdict（可为 signals_rules.parquet）。
        bars: 不复权日线，含 code / date / open / high / low / close / preclose。
        cfg: 参数，默认保守口径。
        start / end: 回测区间（按交易日历裁剪）。

    Returns:
        BacktestResult（资金曲线 + 成交明细）。
    """
    cfg = cfg or BacktestConfig()
    signals = signals.copy()
    signals["date"] = pd.to_datetime(signals["date"])
    if start:
        signals = signals[signals["date"] >= pd.Timestamp(start)]
    if end:
        signals = signals[signals["date"] <= pd.Timestamp(end)]

    bars = bars.copy()
    bars["date"] = pd.to_datetime(bars["date"])
    dates = sorted(bars["date"].unique())
    if start:
        dates = [d for d in dates if d >= pd.Timestamp(start)]
    if end:
        dates = [d for d in dates if d <= pd.Timestamp(end)]

    by_code = prepare_bars(bars)

    # 每日取分数最高的前 N 个 buy 信号（date 作为 T 日，实际在 T+1 开盘成交）
    picks = (
        signals[signals["verdict"] == "buy"]
        .sort_values(["date", "score"], ascending=[True, False])
        .groupby("date", sort=False)
        .head(cfg.top_n_per_day)
    )
    picks_by_date: Dict[pd.Timestamp, List[str]] = {
        d: g["code"].tolist() for d, g in picks.groupby("date", sort=False)
    }
    atr_by_date: Dict[pd.Timestamp, Dict[str, float]] = {}
    if "atr_pct" in picks.columns:
        for d, g in picks.groupby("date", sort=False):
            atr_by_date[d] = dict(zip(g["code"], g["atr_pct"].astype(float)))

    cash = cfg.initial_cash
    positions: List[Position] = []
    trades: List[Trade] = []
    equity_points: Dict[pd.Timestamp, float] = {}

    def bar_row(code: str, d: pd.Timestamp) -> Optional[pd.Series]:
        df = by_code.get(code)
        if df is None or d not in df.index:
            return None
        return df.loc[d]

    for i, d in enumerate(dates):
        # ---------------- 1. 出场 ----------------
        for pos in list(positions):
            row = bar_row(pos.code, d)
            if row is None:               # 停牌：仓位不动
                continue
            pos.hold_days += 1
            exit_price: Optional[float] = None
            reason = ""

            if not np.isfinite(float(row["close"])):   # 坏行/停牌：不动作
                continue
            if row["low"] <= pos.stop:    # 止损（跳空低开则以开盘价成交）
                exit_price = min(pos.stop, float(row["open"]))
                reason = "stop_loss"
            elif row["high"] >= pos.take:  # 止盈（跳空高开则以开盘价成交）
                exit_price = max(pos.take, float(row["open"]))
                reason = "take_profit"
            elif pos.hold_days >= cfg.hold_days:
                exit_price = float(row["close"])
                reason = "hold_expired"

            if exit_price is None:
                continue

            # 跌停卖不掉：优先用数据层预计算的 raw 口径标记，否则按 preclose 估
            if "limit_down" in row.index:
                if bool(row["limit_down"]):
                    continue
            else:
                pre = float(row.get("preclose", 0) or 0)
                if pre > 0 and float(row["close"]) <= pre * (1 - cfg.limit_pct):
                    continue

            filled = exit_price * (1 - cfg.slippage)
            amount = filled * pos.shares
            fee = _commission(amount, cfg) + amount * cfg.stamp_tax
            cash += amount - fee
            cost = pos.entry_price * pos.shares + pos.fee_in
            pnl = amount - fee - cost
            trades.append(
                Trade(
                    code=pos.code,
                    entry_date=pos.entry_date,
                    exit_date=d,
                    entry_price=round(pos.entry_price, 3),
                    exit_price=round(filled, 3),
                    shares=pos.shares,
                    reason=reason,
                    pnl=round(pnl, 2),
                    ret=round(pnl / cost, 4),
                    holding_days=pos.hold_days,
                )
            )
            positions.remove(pos)

        # ---------------- 2. 入场（上一交易日信号，T 日开盘成交） ----------------
        if i > 0:
            prev = dates[i - 1]
            for code in picks_by_date.get(prev, []):
                if len(positions) >= cfg.max_positions:
                    break
                if any(p.code == code for p in positions):
                    continue
                row = bar_row(code, d)
                if row is None:
                    continue
                opn = float(row["open"])
                if not np.isfinite(opn) or opn <= 0:   # 坏行/停牌：无有效开盘价
                    continue
                if cfg.exclude_st and bool(row.get("is_st", False)):
                    continue
                if cfg.min_price > 0 and float(row.get("raw_close", 0) or 0) < cfg.min_price:
                    continue
                # 开盘封涨停 → 买不到（优先用数据层预计算的 raw 口径标记）
                if "limit_up_open" in row.index:
                    if bool(row["limit_up_open"]):
                        continue
                else:
                    pre = float(row.get("preclose", 0) or 0)
                    if pre > 0 and opn >= pre * (1 + cfg.limit_pct):
                        continue

                price = opn * (1 + cfg.slippage)
                budget = cfg.initial_cash * cfg.position_pct
                lots = int(budget // (price * 100))
                if lots <= 0:
                    continue
                shares = lots * 100
                amount = price * shares
                fee = _commission(amount, cfg)
                if amount + fee > cash:      # 现金不足（多笔同时下单时）
                    continue
                cash -= amount + fee
                stop_pct, take_pct = cfg.stop_pct, cfg.take_pct
                atr = atr_by_date.get(prev, {}).get(code)
                if atr and atr > 1.0:      # signals 里 atr_pct 是百分数（5.06 表示 5.06%）
                    atr = atr / 100.0
                if cfg.use_atr_stops and atr and atr > 0:
                    s = min(max(cfg.atr_stop_mult * atr, cfg.atr_min_stop), cfg.atr_max_stop)
                    k = min(cfg.atr_take_mult * atr, cfg.atr_max_take)
                    stop_pct, take_pct = -s, k
                positions.append(
                    Position(
                        code=code,
                        shares=shares,
                        entry_price=price,
                        entry_date=d,
                        stop=round(price * (1 + stop_pct), 2),
                        take=round(price * (1 + take_pct), 2),
                        fee_in=fee,
                    )
                )

        # ---------------- 3. 盯市 ----------------
        mv = 0.0
        for pos in positions:
            row = bar_row(pos.code, d)
            price = float(row["close"]) if row is not None else pos.entry_price
            mv += price * pos.shares
        equity_points[d] = cash + mv

    equity = pd.Series(equity_points).sort_index()
    equity.index.name = "date"
    return BacktestResult(
        equity=equity,
        trades=trades,
        cash_final=cash,
        positions_open=len(positions),
    )
