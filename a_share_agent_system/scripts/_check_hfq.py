# -*- coding: utf-8 -*-
"""_check_hfq.py — 抽样检查 daily_hfq 与 daily_raw × factor 的差异（临时脚本）。"""
import duckdb

con = duckdb.connect(r"data\processed\market_data.duckdb", read_only=True)
rows = con.execute(
    "SELECT r.code, r.date, r.close, r.hfq_factor, h.close AS hfq_close, "
    "ABS(h.close - r.close*r.hfq_factor) AS diff "
    "FROM daily_raw r JOIN daily_hfq h USING(code, date) "
    "WHERE ABS(h.close - r.close*r.hfq_factor) > 1e-6 LIMIT 5"
).fetchall()
for r in rows:
    print(r)
print("样例统计（按日期段）:")
print(con.execute(
    "SELECT MIN(r.date), MAX(r.date), COUNT(*) FROM daily_raw r JOIN daily_hfq h USING(code,date) "
    "WHERE ABS(h.close - r.close*r.hfq_factor) > 1e-6"
).fetchall())
print("差异分布:")
print(con.execute(
    "SELECT CASE WHEN diff < 0.01 THEN '<0.01' WHEN diff < 1 THEN '<1' WHEN diff < 100 THEN '<100' "
    "ELSE '>=100' END b, COUNT(*) FROM (SELECT ABS(h.close - r.close*r.hfq_factor) diff "
    "FROM daily_raw r JOIN daily_hfq h USING(code,date)) GROUP BY 1 ORDER BY 2 DESC"
).fetchall())
