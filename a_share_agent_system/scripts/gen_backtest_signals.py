# -*- coding: utf-8 -*-
"""规则层历史信号生成入口。

用法:
    python scripts/gen_backtest_signals.py                       # 默认 2023-01-01 → 今天
    python scripts/gen_backtest_signals.py --start 2024-01-01
    python scripts/gen_backtest_signals.py --end 2026-06-30 --out data/backtest

产物:
    data/backtest/signals_rules.parquet
        date, code, score, verdict, s_sent, s_trend, s_vol, s_risk,
        close, atr_pct, ret_20d, vol_ratio, net5, n5, veto
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.backtest.signals import DEFAULT_DB, OUT_DIR, build_signals  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="生成规则层历史信号（回测底座）")
    ap.add_argument("--start", default="2023-01-01", help="信号起始日 YYYY-MM-DD")
    ap.add_argument("--end", default=None, help="信号结束日，默认取库内最新交易日")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="DuckDB 路径")
    ap.add_argument("--out", default=str(OUT_DIR), help="输出目录")
    ap.add_argument("--quiet", action="store_true", help="只打印结果")
    args = ap.parse_args()

    t0 = time.time()
    out = build_signals(
        start=args.start,
        end=args.end or time.strftime("%Y-%m-%d"),
        db_path=Path(args.db),
        out_dir=Path(args.out),
        verbose=not args.quiet,
    )
    print(f"耗时 {time.time() - t0:.1f}s / 行数 {len(out):,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
