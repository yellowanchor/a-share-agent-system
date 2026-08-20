"""
QualityReport — 数据质量校验报告生成器
--------------------------------------
基于 DataValidator 的校验结果，生成结构化质量报告。
输出包含覆盖率、缺失值、去重率、异常检测等指标。
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from src.data.validators.data_validator import DataValidator, DataValidationError

logger = logging.getLogger(__name__)


class QualityReport:
    """数据质量校验报告生成器。

    对行情和新闻数据执行多维质量评估，输出可引用的格式化报告。

    Attributes:
        validator: DataValidator 实例。
        output_dir: 报告输出目录。
    """

    def __init__(
        self,
        output_dir: str = "./data/processed",
        max_consecutive_missing: int = 10,
    ) -> None:
        """初始化。

        Args:
            output_dir: 报告输出目录。
            max_consecutive_missing: 最大连续缺失天数。
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.validator = DataValidator(
            max_consecutive_missing=max_consecutive_missing,
        )

    # ------------------------------------------------------------------
    #  行情数据报告
    # ------------------------------------------------------------------

    def generate_kline_report(
        self,
        kline_df: pd.DataFrame,
        expected_start: Optional[str] = None,
        expected_end: Optional[str] = None,
    ) -> Dict[str, Any]:
        """生成日K线行情数据质量报告。

        评估维度:
            1. 覆盖完整性: 实际交易日 vs 预期交易日
            2. 字段质量: 各字段缺失值比例
            3. 价格合理性: OHLC 非正值比例
            4. 成交量质量: 零量/负量比例
            5. 时间连续性: 异常间隔天数

        Args:
            kline_df: 日K线 DataFrame。
            expected_start: 预期起始日期。
            expected_end: 预期截止日期。

        Returns:
            结构化报告字典。
        """
        report = {
            "report_type": "kline_quality",
            "generated_at": datetime.now().isoformat(),
            "data_shape": {
                "rows": len(kline_df),
                "columns": list(kline_df.columns),
            },
            "coverage": {},
            "field_quality": {},
            "price_quality": {},
            "volume_quality": {},
            "time_continuity": {},
            "validation_result": "NOT_RUN",
        }

        if kline_df.empty:
            report["validation_result"] = "EMPTY_DATA"
            return report

        # 1. 覆盖完整性
        report["coverage"] = self._assess_coverage(
            kline_df, expected_start, expected_end
        )

        # 2. 字段缺失率
        report["field_quality"] = self._assess_field_completeness(kline_df)

        # 3. 价格合理性
        report["price_quality"] = self._assess_price_quality(kline_df)

        # 4. 成交量质量
        report["volume_quality"] = self._assess_volume_quality(kline_df)

        # 5. 数据校验
        try:
            self.validator.validate_market_data(kline_df, "report")
            report["validation_result"] = "PASSED"
        except DataValidationError as e:
            report["validation_result"] = "FAILED"
            report["validation_error"] = str(e)

        return report

    def generate_multi_stock_report(
        self,
        kline_df: pd.DataFrame,
        stock_count: Optional[int] = None,
    ) -> Dict[str, Any]:
        """对多股票合并数据生成汇总质量报告。

        Args:
            kline_df: 多股票日K线 DataFrame（MultiIndex: date+code）。
            stock_count: 实际股票数（DataFrame 中提取不到时手工传入）。

        Returns:
            汇总报告字典。
        """
        report = self.generate_kline_report(kline_df)
        report["report_type"] = "multi_stock_kline_quality"

        if not kline_df.empty and isinstance(kline_df.index, pd.MultiIndex):
            codes = kline_df.index.get_level_values("code").unique()
            report["stock_count"] = len(codes)

            # 每只股票记录数统计
            per_stock = kline_df.groupby(level="code").size()
            report["records_per_stock"] = {
                "min": int(per_stock.min()),
                "max": int(per_stock.max()),
                "mean": round(float(per_stock.mean()), 1),
                "median": int(per_stock.median()),
            }

        elif stock_count is not None:
            report["stock_count"] = stock_count

        return report

    # ------------------------------------------------------------------
    #  新闻数据报告
    # ------------------------------------------------------------------

    def generate_news_report(self, news_list: List[Dict]) -> Dict[str, Any]:
        """生成新闻数据质量报告。

        Args:
            news_list: 新闻字典列表。

        Returns:
            结构化报告字典。
        """
        if not news_list:
            return {
                "report_type": "news_quality",
                "generated_at": datetime.now().isoformat(),
                "total_articles": 0,
                "validation_result": "EMPTY_DATA",
            }

        df = pd.DataFrame(news_list)

        report = {
            "report_type": "news_quality",
            "generated_at": datetime.now().isoformat(),
            "total_articles": len(news_list),
            "coverage": {},
            "content_quality": {},
            "source_distribution": {},
        }

        # 按数据源分布
        if "source" in df.columns:
            report["source_distribution"] = df["source"].value_counts().to_dict()

        # 按股票代码分布
        if "stock_code" in df.columns:
            report["stock_distribution"] = (
                df["stock_code"].value_counts().head(20).to_dict()
            )

        # 内容质量
        if "content" in df.columns:
            content_lengths = df["content"].str.len()
            report["content_quality"] = {
                "avg_length": round(float(content_lengths.mean()), 1),
                "min_length": int(content_lengths.min()),
                "max_length": int(content_lengths.max()),
                "empty_rate": round(
                    float((content_lengths == 0).sum() / len(df)) * 100, 2
                ),
            }

        # 时间覆盖
        if "publish_time" in df.columns:
            try:
                times = pd.to_datetime(df["publish_time"], errors="coerce")
                valid_times = times.dropna()
                if not valid_times.empty:
                    report["time_range"] = {
                        "start": valid_times.min().isoformat(),
                        "end": valid_times.max().isoformat(),
                    }
            except Exception:
                pass

        return report

    # ------------------------------------------------------------------
    #  报告输出
    # ------------------------------------------------------------------

    def save_report(self, report: Dict[str, Any], filename: str) -> str:
        """将报告保存为 JSON 文件。

        Args:
            report: 报告字典。
            filename: 文件名（不含路径）。

        Returns:
            输出文件路径。
        """
        path = self.output_dir / filename
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2, default=str)
        logger.info("质量报告已保存: %s", path)
        return str(path)

    def print_summary(self, report: Dict[str, Any]) -> None:
        """控制台打印报告摘要（人类可读格式）。

        Args:
            report: 报告字典。
        """
        print(f"\n{'=' * 50}")
        print(f"  数据质量校验报告")
        print(f"  类型: {report.get('report_type', 'N/A')}")
        print(f"  时间: {report.get('generated_at', 'N/A')}")
        print(f"  结果: {report.get('validation_result', 'N/A')}")
        print(f"{'=' * 50}")

        # 行情报告特有
        if "data_shape" in report:
            print(f"  数据量: {report['data_shape']['rows']} 行")
            print(f"  字段数: {len(report['data_shape']['columns'])}")

        if "stock_count" in report:
            print(f"  股票数: {report['stock_count']} 只")

        if "coverage" in report:
            cov = report["coverage"]
            print(f"  交易日覆盖: {cov.get('trading_days_actual', 'N/A')} / {cov.get('trading_days_expected', 'N/A')}")

        if "field_quality" in report:
            fq = report["field_quality"]
            print(f"  字段完整率:")
            for field, rate in sorted(fq.items()):
                print(f"    {field}: {rate:.1f}%")

        if "price_quality" in report:
            pq = report["price_quality"]
            print(f"  价格异常率: {pq.get('zero_or_negative_rate', 0):.2f}%")

        if "volume_quality" in report:
            vq = report["volume_quality"]
            print(f"  成交量异常率: {vq.get('negative_rate', 0):.2f}%")

        # 新闻报告特有
        if "total_articles" in report:
            print(f"  新闻总数: {report['total_articles']} 条")

        if "source_distribution" in report:
            print(f"  数据源分布: {report['source_distribution']}")

        print()

    # ------------------------------------------------------------------
    #  私有评估方法
    # ------------------------------------------------------------------

    @staticmethod
    def _assess_coverage(
        df: pd.DataFrame, expected_start: Optional[str], expected_end: Optional[str]
    ) -> Dict[str, Any]:
        """评估交易日覆盖。"""
        # 获取实际索引
        if isinstance(df.index, pd.MultiIndex):
            dates = df.index.get_level_values(0).unique()
        else:
            dates = df.index.unique()

        actual_days = len(dates)

        expected_days = None
        if expected_start and expected_end:
            expected_days = len(pd.bdate_range(expected_start, expected_end))

        coverage = {
            "trading_days_actual": actual_days,
            "trading_days_expected": expected_days,
        }
        if expected_days:
            coverage["coverage_rate"] = round(actual_days / expected_days * 100, 1)

        return coverage

    @staticmethod
    def _assess_field_completeness(df: pd.DataFrame) -> Dict[str, float]:
        """评估各字段完整率。"""
        ohclv_fields = ["open", "high", "low", "close", "volume", "amount", "turn"]
        completeness = {}
        for field in ohclv_fields:
            if field in df.columns:
                rate = round((1 - df[field].isna().sum() / len(df)) * 100, 2)
                completeness[field] = rate
        return completeness

    @staticmethod
    def _assess_price_quality(df: pd.DataFrame) -> Dict[str, Any]:
        """评估价格字段质量。"""
        price_cols = ["open", "high", "low", "close"]
        quality = {"fields": {}}

        for col in price_cols:
            if col in df.columns:
                zero_or_neg = (df[col] <= 0).sum()
                quality["fields"][col] = {
                    "invalid_count": int(zero_or_neg),
                    "invalid_rate": round(float(zero_or_neg / len(df)) * 100, 4),
                }

        # 汇总异常率
        total_invalid = sum(
            v["invalid_count"] for v in quality["fields"].values()
        )
        if not df.empty and len(quality["fields"]) > 0:
            rate = float(total_invalid / (len(df) * len(quality["fields"]))) * 100
            quality["zero_or_negative_rate"] = round(rate, 4)
        else:
            quality["zero_or_negative_rate"] = 0.0

        return quality

    @staticmethod
    def _assess_volume_quality(df: pd.DataFrame) -> Dict[str, Any]:
        """评估成交量字段质量。"""
        quality = {}
        if "volume" in df.columns:
            negative = (df["volume"] < 0).sum()
            zero = (df["volume"] == 0).sum()
            quality = {
                "negative_count": int(negative),
                "negative_rate": round(float(negative / len(df)) * 100, 4),
                "zero_count": int(zero),
                "zero_rate": round(float(zero / len(df)) * 100, 4),
            }
        return quality
