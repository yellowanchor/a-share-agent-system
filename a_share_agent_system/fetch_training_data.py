"""
训练数据获取脚本
---------------
一键获取全量 Phase 1 训练数据：
    1. 沪深300全部300只成分股近3年日K线 → Parquet + DuckDB
    2. 财务数据（资产负债表/利润表/现金流量表）
    3. 新闻舆情数据（SimHash去重 + 清洗）
    4. 数据质量校验报告

预计耗时: 300只 × ~2s = 约10-15分钟
"""

import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.utils.logger import setup_logging
from src.utils.storage_monitor import get_disk_usage
from src.data.fetchers.market_data_pipeline import MarketDataPipeline
from src.data.validators.quality_report import QualityReport


async def main() -> None:
    setup_logging(level="INFO", log_file="./logs/training_data.log")

    print("=" * 60)
    print("  训练数据全量获取")
    print(f"  启动: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    pipeline = MarketDataPipeline(
        output_dir="./data/processed",
        n_years=3,
        batch_size=10,
    )

    # 步骤1: 沪深300成分股
    print("\n[1/3] 获取沪深300成分股列表...")
    constituents = pipeline.get_hs300_constituents()
    codes = constituents["code"].tolist()
    print(f"  → {len(codes)} 只股票")

    # 步骤2: 批量获取日K线
    print(f"\n[2/3] 批量获取全量日K线 (分{len(codes)//10}批)...")
    start = (datetime.now().replace(year=datetime.now().year - 3)).strftime("%Y-%m-%d")
    end = datetime.now().strftime("%Y-%m-%d")
    kline_df = await pipeline.fetch_all_kline(codes=codes, start_date=start, end_date=end)

    if kline_df.empty:
        print("  [FAIL] 日K线获取失败，终止")
        return

    parquet_path = pipeline.to_parquet(kline_df, "hs300_3y_daily_kline")

    # 步骤3: DuckDB载入
    print("\n[3/3] DuckDB 载入...")
    pipeline.open_db()
    pipeline.load_to_duckdb(kline_df=kline_df)
    pipeline.close_db()

    # 质量报告
    print("\n生成质量报告...")
    reporter = QualityReport(output_dir="./data/processed")
    report = reporter.generate_multi_stock_report(kline_df)
    reporter.save_report(report, "hs300_quality_report.json")
    reporter.print_summary(report)

    # 磁盘
    disk = get_disk_usage(".")
    print(f"\n磁盘剩余: {disk['free_gb']:.1f} GB")
    print(f"数据文件: {parquet_path}")
    print(f"数据库:   data/processed/market_data.duckdb")
    print("=" * 60)
    print("  训练数据获取完成!")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
