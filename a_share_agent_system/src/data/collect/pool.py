# -*- coding: utf-8 -*-
"""pool.py — 每日新闻采集的"股票池"选择
======================================
全市场 5443 只 × 200 条新闻/天是数据爆炸的根源（≈109 万条/天）。
按股票池采集（300–500 只）既能覆盖所有有交易价值的机会，又把成本压到 1/10。

池子由三部分并集构成：

1. **流动性**：近 20 个交易日成交额 Top N（默认 300）；
2. **异动**：最新交易日涨跌幅绝对值 Top M（默认 100），捕捉突发新闻驱动；
3. **指定**：调用方传入的代码（持仓、自选、Agent 正在分析的票）。

用法::

    from src.data.collect.pool import build_daily_pool
    codes = build_daily_pool(con, top_turnover=300, top_move=100)
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

import duckdb

logger = logging.getLogger(__name__)


def build_daily_pool(
    con: duckdb.DuckDBPyConnection,
    top_turnover: int = 300,
    top_move: int = 100,
    as_of: Optional[str] = None,
    extra_codes: Iterable[str] = (),
) -> list[str]:
    """构造当日新闻采集池。

    Args:
        con: 读写连接。
        top_turnover: 近 20 日成交额 Top N。
        top_move: 最新交易日涨跌幅绝对值 Top M（0 = 关闭）。
        as_of: 基准日 ``YYYY-MM-DD``，None 表示库内最新交易日。
        extra_codes: 额外纳入的 6 位代码。

    Returns:
        6 位股票代码列表（去重、升序）。
    """
    day = as_of or con.execute("SELECT max(date) FROM daily_raw").fetchall()[0][0]
    if not day:
        return sorted({_six(c) for c in extra_codes})

    # 取最近 20 个交易日，先降序再升序，保证是"最近 20 个有行情的交易日"
    trade_days = [r[0] for r in con.execute(
        "SELECT DISTINCT date FROM daily_raw WHERE date <= ? ORDER BY date DESC LIMIT 20",
        [day],
    ).fetchall()]
    if not trade_days:
        return sorted({_six(c) for c in extra_codes})
    start = trade_days[-1]

    out: set[str] = set()

    # 1) 流动性：近 20 日累计成交额 Top N
    #    腾讯源没有逐日成交额（source='tencent' 的行 amount 为 NULL），
    #    用 成交量(股) × 均价 现算代理值；东财源行直接用真实 amount。
    liq = con.execute("""
        SELECT code FROM daily_raw
        WHERE date BETWEEN ? AND ?
          AND (amount IS NOT NULL OR (volume IS NOT NULL AND close IS NOT NULL))
        GROUP BY code
        ORDER BY sum(COALESCE(amount, volume * (high + low + close) / 3.0)) DESC
        LIMIT ?
    """, [start, day, int(top_turnover)]).fetchall()
    out.update(_six(r[0]) for r in liq)

    # 2) 异动：最新交易日涨跌幅绝对值 Top M
    if top_move > 0:
        mv = con.execute("""
            SELECT code FROM daily_raw
            WHERE date = ? AND pct_chg IS NOT NULL AND volume > 0
            ORDER BY abs(pct_chg) DESC
            LIMIT ?
        """, [day, int(top_move)]).fetchall()
        out.update(_six(r[0]) for r in mv)

    # 3) 指定代码
    out.update(_six(c) for c in extra_codes if c)

    codes = sorted(c for c in out if c and len(c) == 6 and c.isdigit())
    logger.info("新闻池: %d 只（流动性 %d + 异动 %d + 指定 %d 去重后）",
                len(codes), top_turnover, top_move, len(list(extra_codes)))
    return codes


def _six(code: str) -> str:
    """``sh.600519`` / ``600519`` → ``600519``。"""
    s = str(code).strip()
    return s.split(".")[-1][:6]
