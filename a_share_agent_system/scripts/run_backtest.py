# -*- coding: utf-8 -*-
"""回测数据装配 + 报告生成。

为什么回测要用前复权价
----------------------
`daily_raw` 在除权除息日会出现机械跳空（例：301139 在 2026-09-30 从 3.31 跌到 0.91，
−72%，A 股根本没有这种跌幅），直接用不复权价回测会把“分红送股”当成暴跌，
凭空产生一堆假止损。所以：

- **价格用 `daily_hfq`**（同一只票的恒因子序列，除权日连续），收益与真实交易等价；
- **涨跌停判定用 `daily_raw`**（真实价格才反映 ±10%/±20% 限制）；
- 金额以“复权元”计价，佣金比例、滑点、印花税不受影响，只有“最低 5 元”这档
  在低价股上会被高估——已写进报告的限制说明。

用法：
    python -u scripts/run_backtest.py --start 2023-01-01 --end 2026-11-23
    python -u scripts/run_backtest.py --start 2026-01-01 --top 5 --hold 20
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, Optional

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.backtest.engine import BacktestConfig, run_backtest  # noqa: E402
from src.backtest.metrics import compute_metrics, equity_frame  # noqa: E402

DB = ROOT / "data" / "processed" / "market_data.duckdb"
SIGNALS = ROOT / "data" / "backtest" / "signals_rules.parquet"
OUT_DIR = ROOT / "data" / "backtest"


def load_bars(start: str, end: str, db: Path = DB) -> pd.DataFrame:
    """装配回测用日线：复权价 + raw 口径的涨跌停标记。"""
    sql = """
    WITH h AS (
        SELECT code, date, open, high, low, close
        FROM daily_hfq
        WHERE date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
    ), r AS (
        SELECT code, date, open, close, is_st,
               lag(close) OVER (PARTITION BY code ORDER BY date) AS prev_close
        FROM daily_raw
        WHERE date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
    )
    SELECT h.code, h.date, h.open, h.high, h.low, h.close,
           r.open AS raw_open, r.close AS raw_close, r.prev_close AS raw_prev_close,
           COALESCE(r.is_st, FALSE) AS is_st,
           (r.open >= r.prev_close * 1.098) AS limit_up_open,
           (r.close <= r.prev_close * 0.902) AS limit_down
    FROM h JOIN r USING (code, date)
    ORDER BY h.date, h.code
    """
    con = duckdb.connect(str(db), read_only=True)
    try:
        df = con.execute(sql, [start, end, start, end]).df()
    finally:
        con.close()
    df["date"] = pd.to_datetime(df["date"])
    for c in ("limit_up_open", "limit_down"):
        df[c] = df[c].fillna(False).astype(bool)

    # 数据清洗：A 股单日涨跌幅不会超过 30%（北交所），超过即视为数据异常
    # （典型来源：退市整理期价格、未及时更新复权因子的除权日）。把这类行的价格置空，
    # 引擎会当作"当日无行情"处理：不进场、不触发止损/止盈、按成本价盯市。
    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    ret = df.groupby("code")["close"].pct_change()
    bad = ret.abs() > 0.31
    if bad.any():
        for c in ("open", "high", "low", "close"):
            df.loc[bad, c] = float("nan")
    return df


def load_signals(path: Path = SIGNALS) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df["date"] = pd.to_datetime(df["date"])
    return df


def baseline_equal_weight(bars: pd.DataFrame) -> pd.Series:
    """基线 1：全市场等权买入持有（每个交易日按当日有行情的股票等权，日收益取均值）。"""
    piv = bars.pivot_table(index="date", columns="code", values="close")
    rets = piv.pct_change()
    daily = rets.mean(axis=1).fillna(0.0)
    return (1 + daily).cumprod() * 250_000.0


def baseline_random(bars: pd.DataFrame, n: int, top: int, hold: int = 20, seed: int = 42,
                    rounds: int = 20) -> Dict[str, float]:
    """基线 2：同池随机选股（蒙特卡洛）。

    以「每日随机取 N 只、持有 hold 日」近似随机策略，重复 rounds 次取均值，
    用来回答"策略的收益是不是运气"。
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    piv = bars.pivot_table(index="date", columns="code", values="close")
    dates = piv.index
    totals = []
    for _ in range(rounds):
        equity = 1.0
        i = 0
        while i < len(dates) - hold:
            row = piv.iloc[i]
            codes = row.dropna().index
            if len(codes) == 0:
                i += 1
                continue
            pick = rng.choice(codes, size=min(top, len(codes)), replace=False)
            entry = row[pick].values
            exit_px = piv.iloc[i + hold][pick].values
            ok = (entry > 0) & ~pd.isna(exit_px)
            if ok.any():
                ret = (exit_px[ok] / entry[ok] - 1).mean()
                equity *= (1 + ret * 0.2 * min(len(entry[ok]), 5))
            i += hold
        totals.append(equity)
    arr = np.array(totals)
    return {
        "mean": float(arr.mean() - 1),
        "p10": float(np.percentile(arr, 10) - 1),
        "p90": float(np.percentile(arr, 90) - 1),
        "rounds": rounds,
    }


def fmt_pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def build_report(m: Dict[str, float], base_ew: Dict[str, float], base_rand: Dict[str, float],
                 cfg: BacktestConfig, start: str, end: str, n_days: int) -> str:
    lines = []
    lines.append("# 规则层回测报告")
    lines.append("")
    lines.append(f"- 区间：{start} ~ {end}（{n_days} 个交易日）")
    lines.append(f"- 初始资金：{cfg.initial_cash:,.0f} 元；单票 {cfg.position_pct:.0%}；最多 {cfg.max_positions} 只；"
                 f"每日取分数前 {cfg.top_n_per_day} 个 buy 信号；持有上限 {cfg.hold_days} 交易日")
    lines.append(f"- 成本：佣金 {cfg.commission:.4%}（最低 {cfg.min_commission:.0f} 元）+ 卖出印花税 "
                 f"{cfg.stamp_tax:.2%} + 单边滑点 {cfg.slippage:.2%}")
    lines.append("")
    lines.append("## 1. 策略 vs 基线")
    lines.append("")
    lines.append("| 指标 | 策略 | 全市场等权 | 随机选股（蒙特卡洛） |")
    lines.append("|---|---|---|---|")
    lines.append(f"| 累计收益 | {fmt_pct(m['total_return'])} | {fmt_pct(base_ew['total_return'])} | "
                 f"{fmt_pct(base_rand['mean'])}（p10 {fmt_pct(base_rand['p10'])} / p90 {fmt_pct(base_rand['p90'])}） |")
    lines.append(f"| 年化收益 | {fmt_pct(m['cagr'])} | {fmt_pct(base_ew['cagr'])} | — |")
    lines.append(f"| 最大回撤 | {fmt_pct(m['max_drawdown'])} | {fmt_pct(base_ew['max_drawdown'])} | — |")
    lines.append(f"| Sharpe | {m['sharpe']:.2f} | {base_ew['sharpe']:.2f} | — |")
    lines.append(f"| 胜率 | {fmt_pct(m['win_rate'])}（{int(m['n_trades'])} 笔） | — | — |")
    lines.append(f"| 盈亏比 | {m['profit_factor']:.2f} | — | — |")
    lines.append(f"| 平均持有 | {m['avg_holding_days']:.1f} 交易日 | — | — |")
    lines.append("")
    lines.append("## 2. 结论")
    lines.append("")
    beat_ew = m["total_return"] > base_ew["total_return"]
    beat_rand = m["total_return"] > base_rand["mean"]
    lines.append(f"- 相对全市场等权：{'跑赢' if beat_ew else '跑输'}"
                 f"（{fmt_pct(m['total_return'])} vs {fmt_pct(base_ew['total_return'])}）")
    lines.append(f"- 相对随机选股：{'跑赢' if beat_rand else '跑输'}"
                 f"（{fmt_pct(m['total_return'])} vs {fmt_pct(base_rand['mean'])}）")
    lines.append("")
    lines.append("## 3. 口径与限制")
    lines.append("")
    lines.append("- 价格用 `daily_hfq`（连续复权序列），涨跌停用 `daily_raw` 判定；金额为「复权元」，")
    lines.append("  佣金比例/印花税/滑点真实，只有「最低 5 元」在低价股上可能被低估。")
    lines.append("- 信号 = 收盘后生成，**次日开盘价**成交；开盘封涨停跳过，跌停当日不卖、顺延。")
    lines.append("- 2026-02 之前没有新闻覆盖，情感因子恒为 50（等价三因子）；分段结论见 §4。")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="规则层回测")
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--end", default="2026-11-23")
    ap.add_argument("--top", type=int, default=5, help="每日取前 N 个信号")
    ap.add_argument("--hold", type=int, default=20, help="持有上限（交易日）")
    ap.add_argument("--cash", type=float, default=250_000.0)
    ap.add_argument("--out", default=str(OUT_DIR / "backtest_report.md"))
    args = ap.parse_args()

    cfg = BacktestConfig(initial_cash=args.cash, top_n_per_day=args.top, hold_days=args.hold)
    print(f"[1/4] 读取行情 {args.start} ~ {args.end} ...")
    bars = load_bars(args.start, args.end)
    print(f"      {len(bars):,} 行 / {bars['code'].nunique()} 只 / {bars['date'].nunique()} 个交易日")
    print("[2/4] 读取历史信号 ...")
    sig = load_signals()
    print("[3/4] 回测 ...")
    res = run_backtest(sig, bars, cfg, start=args.start, end=args.end)
    m = compute_metrics(res.equity, res.trades_df)
    print(f"      成交 {len(res.trades)} 笔 / 累计 {m['total_return'] * 100:.2f}% / 最大回撤 {m['max_drawdown'] * 100:.2f}%")

    print("[4/4] 基线 ...")
    eq_ew = baseline_equal_weight(bars)
    base_ew = compute_metrics(eq_ew, pd.DataFrame())
    base_rand = baseline_random(bars, n=bars["code"].nunique(), top=cfg.top_n_per_day, hold=cfg.hold_days)

    report = build_report(m, base_ew, base_rand, cfg, args.start, args.end, bars["date"].nunique())
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")

    eq = equity_frame(res)
    eq.to_parquet(OUT_DIR / "equity_curve.parquet", index=False)
    res.trades_df.to_parquet(OUT_DIR / "trades.parquet", index=False)
    print(f"      报告：{out}")
    print(f"      曲线：{OUT_DIR / 'equity_curve.parquet'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
