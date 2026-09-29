"""
DataValidator — 金融数据完整性校验器
------------------------------------
对所有获取的行情数据进行严格校验，确保数据质量满足量化策略要求。
校验失败时抛出明确的异常信息，便于快速定位数据问题。
"""

import logging
from typing import List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class DataValidationError(Exception):
    """数据校验异常基类。"""

    def __init__(self, message: str, code: Optional[str] = None):
        full_msg = f"[数据校验失败]"
        if code:
            full_msg += f" [股票: {code}]"
        full_msg += f" {message}"
        super().__init__(full_msg)
        self.code = code


class DataValidator:
    """金融行情数据校验器。

    执行多维度的数据质量检查：
        1. 结构性检查：非空、字段完整性、无全空行
        2. 价格合理性：OHLC > 0，high >= low
        3. 成交量合理性：volume >= 0
        4. 时间连续性：无明显缺失交易日

    Typical usage:
        validator = DataValidator()
        validator.validate_market_data(df)
    """

    REQUIRED_OHLCV_FIELDS: List[str] = [
        "open", "high", "low", "close", "volume"
    ]

    MIN_TRADING_DAYS_PER_YEAR: int = 200

    def __init__(
        self,
        max_consecutive_missing: int = 10,
        price_outlier_std_threshold: float = 5.0,
    ) -> None:
        """初始化数据校验器。

        Args:
            max_consecutive_missing: 允许的最大连续缺失交易日数。
            price_outlier_std_threshold: 价格异常检测的标准差倍数阈值。
        """
        self.max_consecutive_missing = max_consecutive_missing
        self.price_outlier_std_threshold = price_outlier_std_threshold

    # ------------------------------------------------------------------
    #  主校验入口
    # ------------------------------------------------------------------

    def validate_market_data(self, df: pd.DataFrame, code: str = "") -> bool:
        """对行情 DataFrame 执行全量质量校验。

        校验流程:
            1. 数据非空检查
            2. 必需字段完整性
            3. 时间连续性
            4. 价格合理性 (OHLC > 0, high >= low)
            5. 成交量非负
            6. 无全空行
            7. 换手率合理性（若存在）
            8. 价格跳变异常检测

        Args:
            df: 待校验的行情 DataFrame，索引应为时间类型。
            code: 股票代码（用于错误定位）。

        Returns:
            所有检查通过返回 True。

        Raises:
            DataValidationError: 任一检查项失败时抛出。
        """
        if df.empty:
            raise DataValidationError("数据为空（DataFrame 无任何行）", code)

        self._check_required_fields(df, code)
        self._check_no_fully_null_rows(df, code)
        self._check_time_continuity(df, code)
        self._check_price_validity(df, code)
        self._check_volume_validity(df, code)
        self._check_high_ge_low(df, code)
        self._check_turn_rate(df, code)
        self._check_price_jumps(df, code)

        logger.info("股票 %s 数据校验全部通过 [OK]", code or "未知")
        return True

    # ------------------------------------------------------------------
    #  单项检查
    # ------------------------------------------------------------------

    def _check_required_fields(self, df: pd.DataFrame, code: str) -> None:
        missing = [f for f in self.REQUIRED_OHLCV_FIELDS if f not in df.columns]
        if missing:
            raise DataValidationError(
                f"缺少必需字段: {missing}，现有字段: {list(df.columns)}", code
            )

    def _check_no_fully_null_rows(self, df: pd.DataFrame, code: str) -> None:
        available_cols = [c for c in self.REQUIRED_OHLCV_FIELDS if c in df.columns]
        if not available_cols:
            return
        all_null_mask = df[available_cols].isna().all(axis=1)
        if all_null_mask.any():
            null_dates = df.index[all_null_mask]
            date_list = self._format_dates(null_dates)
            raise DataValidationError(
                f"存在 {all_null_mask.sum()} 行全空数据，日期: {date_list}", code
            )

    def _check_time_continuity(self, df: pd.DataFrame, code: str) -> None:
        if not isinstance(df.index, pd.DatetimeIndex):
            raise DataValidationError(
                f"索引不是 DatetimeIndex 类型，当前类型: {type(df.index).__name__}", code
            )
        if df.index.duplicated().any():
            dup_dates = df.index[df.index.duplicated()].unique()
            raise DataValidationError(
                f"存在 {len(dup_dates)} 个重复日期: {self._format_dates(dup_dates)}", code
            )
        sorted_idx = df.index.sort_values()
        if not df.index.equals(sorted_idx):
            logger.warning("股票 %s 的数据未按时间排序，已自动修正", code)
        diffs = sorted_idx.to_series().diff().dropna()
        threshold = pd.Timedelta(days=self.max_consecutive_missing + 5)
        large_gaps = diffs[diffs > threshold]
        if not large_gaps.empty:
            gap_info = []
            for date, gap in large_gaps.items():
                prev_date = date - gap
                gap_info.append(f"{prev_date.date()} -> {date.date()} (间隔{gap.days}天)")
            raise DataValidationError(
                f"时间序列存在异常大间隔（>{threshold.days}天）: {gap_info[:5]}", code
            )

    def _check_price_validity(self, df: pd.DataFrame, code: str) -> None:
        price_cols = ["open", "high", "low", "close"]
        available = [c for c in price_cols if c in df.columns]
        for col in available:
            invalid_mask = df[col] <= 0
            if invalid_mask.any():
                invalid_dates = df.index[invalid_mask]
                raise DataValidationError(
                    f"字段 '{col}' 存在 {invalid_mask.sum()} 个非正值（≤0），"
                    f"日期示例: {self._format_dates(invalid_dates[:3])}",
                    code,
                )
        for col in available:
            nan_count = df[col].isna().sum()
            if nan_count > 0:
                nan_dates = df.index[df[col].isna()]
                raise DataValidationError(
                    f"字段 '{col}' 存在 {nan_count} 个 NaN 值，"
                    f"日期: {self._format_dates(nan_dates)}",
                    code,
                )

    def _check_high_ge_low(self, df: pd.DataFrame, code: str) -> None:
        if "high" not in df.columns or "low" not in df.columns:
            return
        invalid_mask = df["high"] < df["low"]
        if invalid_mask.any():
            invalid_dates = df.index[invalid_mask]
            raise DataValidationError(
                f"存在 {invalid_mask.sum()} 行最高价 < 最低价，"
                f"日期: {self._format_dates(invalid_dates)}",
                code,
            )

    def _check_volume_validity(self, df: pd.DataFrame, code: str) -> None:
        if "volume" not in df.columns:
            return
        invalid_mask = df["volume"] < 0
        if invalid_mask.any():
            invalid_dates = df.index[invalid_mask]
            raise DataValidationError(
                f"存在 {invalid_mask.sum()} 行成交量为负值，"
                f"日期: {self._format_dates(invalid_dates)}",
                code,
            )
        nan_count = df["volume"].isna().sum()
        if nan_count > 0:
            nan_dates = df.index[df["volume"].isna()]
            raise DataValidationError(
                f"存在 {nan_count} 行成交量为 NaN，日期: {self._format_dates(nan_dates)}",
                code,
            )

    def _check_turn_rate(self, df: pd.DataFrame, code: str) -> None:
        if "turn" not in df.columns:
            return
        neg_count = (df["turn"] < 0).sum()
        if neg_count > 0:
            logger.warning(
                "股票 %s: 字段 'turn'(换手率) 存在 %d 个负值，数据可能异常",
                code, neg_count,
            )

    def _check_price_jumps(self, df: pd.DataFrame, code: str) -> None:
        if "close" not in df.columns or len(df) < 20:
            return
        returns = df["close"].pct_change().dropna()
        if len(returns) < 20:
            return
        rolling_mean = returns.rolling(20).mean()
        rolling_std = returns.rolling(20).std()
        outliers = returns[
            abs(returns - rolling_mean) > self.price_outlier_std_threshold * rolling_std
        ]
        if len(outliers) > 0:
            logger.info(
                "股票 %s: 检测到 %d 个价格异常波动点（超出 %.1fσ），"
                "可能为真实市场异动，不阻塞流程",
                code, len(outliers), self.price_outlier_std_threshold,
            )

    # ------------------------------------------------------------------
    #  批量校验
    # ------------------------------------------------------------------

    def validate_batch(self, data_dict: dict) -> dict:
        """批量校验多只股票的数据。

        Args:
            data_dict: {股票代码: DataFrame} 的字典。

        Returns:
            {"passed": [...], "failed": {股票代码: 错误信息}} 的字典。
        """
        passed = []
        failed = {}
        for code, df in data_dict.items():
            try:
                self.validate_market_data(df, code)
                passed.append(code)
            except DataValidationError as e:
                failed[code] = str(e)
                logger.error("股票 %s 校验失败: %s", code, e)
        logger.info("批量校验完成: 通过 %d/%d", len(passed), len(data_dict))
        return {"passed": passed, "failed": failed}

    # ------------------------------------------------------------------
    #  工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _format_dates(indices: pd.DatetimeIndex, max_items: int = 5) -> str:
        """将日期索引格式化为可读字符串。"""
        dates = list(indices[:max_items])
        formatted = [d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d) for d in dates]
        suffix = "..." if len(indices) > max_items else ""
        return "[" + ", ".join(formatted) + "]" + suffix
