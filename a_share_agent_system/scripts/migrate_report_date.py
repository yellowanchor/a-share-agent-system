# -*- coding: utf-8 -*-
"""一次性迁移：financials_yjbb.report_date VARCHAR('20160331') → DATE。

背景：源 parquet 里 report_date 是 8 位字符串，前端与报告直接显示成 20160331，很丑，
且无法与 pub_date(DATE) 一起做日期运算。此脚本只改类型，不改行数。

注意：该列上有索引，必须先 DROP INDEX 再 ALTER，改完重建索引。
"""
import duckdb

DB = r"data/processed/market_data.duckdb"

con = duckdb.connect(DB)
before = con.execute("SELECT count(*) FROM financials_yjbb").fetchall()[0][0]
print("迁移前行数:", before)

idx_rows = con.execute(
    "SELECT index_name, sql FROM duckdb_indexes() WHERE table_name = 'financials_yjbb'"
).fetchall()
print("现有索引:", idx_rows)

for name, _sql in idx_rows:
    con.execute(f"DROP INDEX IF EXISTS {name}")
    print("已删除索引:", name)

con.execute(
    "ALTER TABLE financials_yjbb ALTER COLUMN report_date TYPE DATE "
    "USING strptime(report_date, '%Y%m%d')::DATE"
)
con.execute("CREATE INDEX IF NOT EXISTS idx_financials_yjbb_code_report_date "
            "ON financials_yjbb(code, report_date)")
con.execute("CHECKPOINT")

after = con.execute("SELECT count(*) FROM financials_yjbb").fetchall()[0][0]
print("迁移后行数:", after)
print("迁移后类型:", [r for r in con.execute("DESCRIBE financials_yjbb").fetchall() if r[0] == "report_date"])
print("样本:", con.execute(
    "SELECT code, name, report_date, pub_date FROM financials_yjbb ORDER BY pub_date DESC LIMIT 3"
).fetchall())
print("一致:", before == after)
con.close()
