"""run_test — 验收测试脚本

验证 Phase 1 数据基础建设是否就绪：
    1. 目录结构完整性
    2. 模块导入正确性
    3. A股数据获取与校验（BaoStock 真实数据）

Usage:
    cd a_share_agent_system
    python run_test.py
"""

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.data.fetchers import AShareFetcher, HKStockFetcher
from src.data.validators import DataValidator, DataValidationError
from src.agents import BaseAgent
from src.utils.logger import setup_logging
from src.utils.storage_monitor import get_disk_usage, get_gpu_memory


def check_directory_structure() -> bool:
    """检查项目目录结构是否完整。

    Returns:
        所有必需目录都存在返回 True。
    """
    required_dirs = [
        "configs", "configs/prompts",
        "data/raw", "data/processed", "data/vector_store",
        "src/data/fetchers", "src/data/processors", "src/data/validators",
        "src/models/sentiment", "src/models/rag",
        "src/agents", "src/utils",
        "notebooks", "tests",
    ]
    root = Path(__file__).resolve().parent
    all_ok = True
    for d in required_dirs:
        p = root / d
        if p.is_dir():
            print(f"  [OK] {d}/")
        else:
            print(f"  [MISSING] {d}/")
            all_ok = False
    return all_ok


def check_imports() -> bool:
    """检查所有核心模块能否正确导入。

    Returns:
        全部导入成功返回 True。
    """
    modules_to_check = [
        ("src.data.fetchers", ["BaseFetcher", "AShareFetcher", "HKStockFetcher"]),
        ("src.data.validators", ["DataValidator", "DataValidationError"]),
        ("src.agents", ["BaseAgent"]),
        ("src.utils.logger", ["setup_logging"]),
        ("src.utils.storage_monitor", ["check_resources", "report"]),
    ]
    all_ok = True
    for module_path, names in modules_to_check:
        try:
            mod = __import__(module_path, fromlist=names)
            for name in names:
                getattr(mod, name)
            print(f"  [OK] {module_path} ({', '.join(names)})")
        except Exception as e:
            print(f"  [FAIL] {module_path}: {e}")
            all_ok = False
    return all_ok


async def check_data_pipeline() -> bool:
    """端到端数据流水线测试：获取贵州茅台日K线并校验。

    Returns:
        数据获取+校验全部通过返回 True。
    """
    code = "sh.600519"
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

    print(f"\n  获取 {code} 近90天数据 ({start_date} ~ {end_date})...")

    try:
        async with AShareFetcher(cache_dir="./data/processed") as fetcher:
            df = await fetcher.get_data(code, start_date, end_date)

        if df.empty:
            print("  [FAIL] 返回空数据")
            return False

        print(f"  [OK] 获取成功: {len(df)} 条记录")

        validator = DataValidator()
        validator.validate_market_data(df, code)
        print(f"  [OK] 数据校验通过: "
              f"收盘 {df['close'].min():.2f} ~ {df['close'].max():.2f}, "
              f"日均量 {df['volume'].mean():,.0f}")

        return True
    except Exception as e:
        print(f"  [FAIL] {e}")
        return False


def check_resources() -> None:
    """报告系统资源状态。"""
    print()
    disk = get_disk_usage(".")
    print(f"  磁盘: {disk['free_gb']:.1f} GB 可用 / {disk['total_gb']:.1f} GB 总量 ({disk['percent']}% 已用)")

    gpu = get_gpu_memory()
    if gpu:
        print(f"  GPU:  {gpu['free_mb']:.0f} MB 可用 / {gpu['total_mb']:.0f} MB 总量")


async def main() -> None:
    """验收测试主函数。"""
    print("=" * 60)
    print("  Phase 1 数据基础建设 — 验收测试")
    print("=" * 60)

    # 1. 目录结构
    print("\n[1/4] 检查目录结构...")
    struct_ok = check_directory_structure()

    # 2. 模块导入
    print("\n[2/4] 检查模块导入...")
    import_ok = check_imports()

    # 3. 资源状态
    print("\n[3/4] 系统资源状态...")
    check_resources()

    # 4. 数据流水线
    print("\n[4/4] 数据流水线端到端测试...")
    # 配置日志
    setup_logging(level="WARNING", log_file="./logs/test.log")

    data_ok = await check_data_pipeline()

    # 汇总
    print("\n" + "=" * 60)
    results = {
        "目录结构": struct_ok,
        "模块导入": import_ok,
        "数据流水线": data_ok,
    }
    passed = sum(results.values())
    total = len(results)
    for name, ok in results.items():
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}")
    print(f"\n  结果: {passed}/{total} 项通过")
    if passed == total:
        print("  === Phase 1 验收通过! ===")
    else:
        print("  === 存在未通过项，请检查 ===")
    print("=" * 60)


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
