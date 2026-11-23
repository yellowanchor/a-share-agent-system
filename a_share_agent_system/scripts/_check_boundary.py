# -*- coding: utf-8 -*-
"""_check_boundary.py — 校验新增行与库内旧行的连续性与复权一致性（临时脚本）。"""
import sys

import duckdb

DB = r"data\processed\market_data.duckdb"
CODES = sys.argv[1].split(",") if len(sys.argv) > 1 else ["sh.600519", "sz.000001", "sz.002415"]

con = duckdb.connect(DB, read_only=True)
for code in CODES:
    print(code)
    for r in con.execute(
        "SELECT date, close, preclose, hfq_factor, volume, source FROM daily_raw "
        "WHERE code=? ORDER BY date DESC LIMIT 6", [code]
    ).fetchall():
        print("   ", r)
    # 涨跌幅与 (close/preclose-1) 是否一致
    bad = con.execute(
        "SELECT date, close, preclose, pct_chg FROM daily_raw "
        "WHERE code=? AND date >= '2026-08-20' AND preclose IS NOT NULL "
        "AND ABS((close/preclose-1)*100 - pct_chg) > 0.02", [code]
    ).fetchall()
    print("    涨跌幅不一致行:", len(bad), bad[:3])
    mism = con.execute(
        "SELECT COUNT(*) FROM daily_raw r JOIN daily_hfq h USING(code, date) "
        "WHERE r.code=? AND ABS(h.close - r.close*r.hfq_factor) > 1e-6", [code]
    ).fetchall()[0][0]
    print("    hfq 不一致行:", mism)
print("全库 hfq 不一致:",
      con.execute(
          "SELECT COUNT(*) FROM daily_raw r JOIN daily_hfq h USING(code, date) "
          "WHERE ABS(h.close - r.close*r.hfq_factor) > 1e-6"
      ).fetchall()[0][0])
print("最新交易日:", con.execute("SELECT MAX(date) FROM daily_raw").fetchall()[0][0])
print("(code,date) 重复:", con.execute(
    "SELECT COUNT(*) FROM (SELECT code, date, COUNT(*) c FROM daily_raw GROUP BY 1,2 HAVING c>1)").fetchall()[0][0])
