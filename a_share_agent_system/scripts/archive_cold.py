# -*- coding: utf-8 -*-
"""archive_cold.py — 冷热分层归档（每月跑一次，防止数据库无限膨胀）
=================================================================
策略：

* **新闻**：只保留近 ``--days`` 天（默认 120）在 DuckDB 热库，更早的按月
  导出到 ``data/cold/news_YYYY_MM.parquet`` 后从热库删除；
* **行情**：不迁移。全市场每天只增 ~1.1 万行（≈1 MB），5 年也才 2 GB 级，
  而回测/指标计算强依赖连续历史，挪走反而拖慢查询；
* **落盘收敛**：删除后 `CHECKPOINT`，必要时 `--compact` 重写新闻表回收空洞。

用法::

    python scripts/archive_cold.py --dry-run          # 先看会搬走多少
    python scripts/archive_cold.py --days 120         # 正式执行
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import duckdb  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "processed" / "market_data.duckdb"
COLD_DIR = ROOT / "data" / "cold"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("archive_cold")


def main() -> None:
    ap = argparse.ArgumentParser(description="新闻冷数据归档（按月导出 parquet 后删除）")
    ap.add_argument("--days", type=int, default=120, help="热库保留天数")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--out", default=str(COLD_DIR), help="冷库目录")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--compact", action="store_true", help="删除后重写新闻表回收空间")
    args = ap.parse_args()

    db = Path(args.db)
    out_dir = Path(args.out)
    if not db.exists():
        raise SystemExit(f"DuckDB 库不存在: {db}")

    cutoff = (date.today() - timedelta(days=args.days)).isoformat()
    logger.info("热库保留 %d 天 → 归档 publish_time < %s 的新闻", args.days, cutoff)

    con = duckdb.connect(str(db))
    con.execute("SET memory_limit='16GB'")

    total_before = con.execute("SELECT count(*) FROM news_articles").fetchall()[0][0]
    to_archive = con.execute(
        "SELECT count(*) FROM news_articles WHERE publish_time < ?", [cutoff]
    ).fetchall()[0][0]
    logger.info("新闻总量 %d，待归档 %d，保留 %d",
                total_before, to_archive, total_before - to_archive)

    if to_archive == 0:
        logger.info("没有需要归档的数据")
        con.close()
        return

    months = [r[0] for r in con.execute(
        "SELECT DISTINCT substr(publish_time, 1, 7) FROM news_articles "
        "WHERE publish_time < ? ORDER BY 1", [cutoff],
    ).fetchall()]
    logger.info("涉及月份: %s", ", ".join(months))

    if args.dry_run:
        for m in months:
            n = con.execute(
                "SELECT count(*) FROM news_articles WHERE substr(publish_time,1,7)=? AND publish_time < ?",
                [m, cutoff],
            ).fetchall()[0][0]
            logger.info("[dry-run] %s → %s（%d 条）", m, out_dir / f"news_{m}.parquet", n)
        con.close()
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    size_before = db.stat().st_size
    for m in months:
        target = out_dir / f"news_{m}.parquet"
        if target.exists():
            logger.info("已存在，跳过导出: %s", target.name)
        else:
            con.execute(f"""
                COPY (SELECT * FROM news_articles
                      WHERE substr(publish_time,1,7) = '{m}' AND publish_time < '{cutoff}')
                TO '{target.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """)
            logger.info("导出 %s（%.1f MB）", target.name, target.stat().st_size / 1e6)

    con.execute("DELETE FROM news_articles WHERE publish_time < ?", [cutoff])
    if args.compact:
        logger.info("重写新闻表回收空间…")
        con.execute("CREATE OR REPLACE TABLE news_articles AS SELECT * FROM news_articles")
    con.execute("CHECKPOINT")

    total_after = con.execute("SELECT count(*) FROM news_articles").fetchall()[0][0]
    size_after = db.stat().st_size
    logger.info("完成：新闻 %d → %d 行；库文件 %.1f MB → %.1f MB",
                total_before, total_after, size_before / 1e6, size_after / 1e6)
    con.close()


if __name__ == "__main__":
    main()
