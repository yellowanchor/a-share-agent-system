# -*- coding: utf-8 -*-
"""
build_market_db.py — 数据平台 → DuckDB 结构化入库
===================================================
将 data_platform 产出的最终 Parquet 数据集物化为 DuckDB 数据库，
供 Agent 系统高频查询（行情 / 财务 / 新闻）。

表结构:
    daily_hfq      前复权日线 (19 列, 含 hfq_factor)
    daily_raw      不复权日线 (19 列)
    financials_yjbb 业绩报表 (17 列, Point-in-Time)
    news_articles  新闻语料 (title/text/stock_code/source/publish_time/url)

用法:
    python build_market_db.py [--src DIR] [--out PATH] [--rebuild]

说明:
    - 默认 --src 指向 data_platform/data/processed
    - 默认 --out 为 ./data/processed/market_data.duckdb (a_share_agent_system 下)
    - --rebuild 强制删除旧库重建（幂等）
"""

import argparse
import logging
import sys
import time
from pathlib import Path

import duckdb

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("build_market_db")

# 项目根目录（脚本位于 a_share_agent_system/scripts/）
ROOT = Path(__file__).resolve().parents[1]

# 各表对应源文件与主键/索引设计
TABLES = {
    "daily_hfq": {
        "file": "daily_hfq.parquet",
        "index": ["code", "date"],
        "comment": "前复权日线",
    },
    "daily_raw": {
        "file": "daily_raw.parquet",
        "index": ["code", "date"],
        "comment": "不复权日线",
    },
    "financials_yjbb": {
        "file": "financials_yjbb.parquet",
        "index": ["code", "report_date"],
        "comment": "业绩报表(Point-in-Time)",
    },
    "news_articles": {
        "file": "news.parquet",
        "index": ["stock_code", "publish_time"],
        "comment": "新闻语料",
    },
}


def build(src_dir: Path, out_path: Path, rebuild: bool) -> None:
    """执行 DuckDB 建库。"""
    src_dir = src_dir.resolve()
    if not src_dir.is_dir():
        logger.error("源目录不存在: %s", src_dir)
        sys.exit(1)

    if rebuild and out_path.exists():
        # 不物理删除文件（沙箱回收站限制），通过表级 DROP+CREATE 实现幂等重建
        logger.warning("--rebuild: 表级重建 %s (不删除文件)", out_path)

    out_path.parent.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    con = duckdb.connect(str(out_path))
    con.execute("SET memory_limit='16GB'")
    con.execute("SET threads=8")

    try:
        for table, cfg in TABLES.items():
            src_file = src_dir / cfg["file"]
            if not src_file.exists():
                logger.error("源文件缺失: %s", src_file)
                sys.exit(1)

            # 幂等：表存在则先删
            con.execute(f"DROP TABLE IF EXISTS {table}")

            logger.info("[%s] 从 %s 导入 ...", table, src_file.name)
            t1 = time.time()
            con.execute(
                f"""
                CREATE TABLE {table} AS
                SELECT * FROM read_parquet('{src_file.as_posix()}')
                """
            )
            n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            cols = con.execute(f"SELECT COUNT(*) FROM information_schema.columns "
                               f"WHERE table_name='{table}'").fetchone()[0]
            logger.info("[%s] 导入完成: %d 行 × %d 列, 耗时 %.1fs",
                        table, n, cols, time.time() - t1)

            # 建索引（加速按 code 查询）
            for col in cfg["index"]:
                idx_name = f"idx_{table}_{col}"
                con.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({col})")
                logger.info("[%s] 索引 %s 已创建", table, idx_name)

        # 汇总
        total = 0
        for table in TABLES:
            n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            total += n
        size_mb = out_path.stat().st_size / 1e6

        logger.info("=" * 60)
        logger.info("DuckDB 建库完成:")
        logger.info("  库文件    : %s (%.1f MB)", out_path, size_mb)
        logger.info("  总记录数  : %d", total)
        logger.info("  总耗时    : %.1fs", time.time() - t0)
        logger.info("=" * 60)

    except Exception as e:
        logger.error("建库失败: %s", e)
        sys.exit(1)
    finally:
        con.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="数据平台 → DuckDB 入库")
    parser.add_argument(
        "--src",
        type=Path,
        default=ROOT.parent / "data_platform" / "data" / "processed",
        help="数据平台 processed 目录",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data" / "processed" / "market_data.duckdb",
        help="输出 DuckDB 文件路径",
    )
    parser.add_argument("--rebuild", action="store_true", help="强制删除旧库重建")
    args = parser.parse_args()

    logger.info("DuckDB 版本: %s", duckdb.__version__)
    build(args.src, args.out, args.rebuild)
