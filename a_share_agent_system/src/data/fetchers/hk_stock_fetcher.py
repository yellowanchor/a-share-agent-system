"""
HKStockFetcher — 港股数据获取器
-------------------------------
港股日K线行情获取器，统一货币为 HKD，自动过滤低流动性标的。

数据源策略:
    1. 富途OpenAPI（Futu OpenD）: 生产首选 — 论文白名单
    2. AkShare（东方财富数据源）: 国内免费开源，当前实现
"""

import logging
from typing import Optional

import pandas as pd

from src.data.fetchers.base_fetcher import BaseFetcher

logger = logging.getLogger(__name__)


class HKStockFetcher(BaseFetcher):
    """港股日K线行情获取器。

    通过 AkShare 获取港股历史行情数据（OHLCV + PE），
    支持低流动性过滤、货币标准化、整手交易查询。

    Attributes:
        frequency: K线周期，默认 "d"（日线）。
        min_daily_turnover: 日均成交额阈值（港元）。
    """

    MIN_DAILY_TURNOVER_HKD: float = 1_000_000.0

    def __init__(
        self,
        frequency: str = "d",
        min_daily_turnover: float = 1_000_000.0,
        cache_dir: str = "./data/processed",
        max_retries: int = 3,
    ) -> None:
        """初始化 HKStockFetcher。

        Args:
            frequency: K线周期: "d"=日线, "w"=周线, "m"=月线。
            min_daily_turnover: 日均成交额最低阈值（港元），默认 100 万。
            cache_dir: 缓存目录。
            max_retries: 最大重试次数。
        """
        super().__init__(cache_dir=cache_dir, max_retries=max_retries)
        self.frequency = frequency
        self.min_daily_turnover = min_daily_turnover

    async def __aenter__(self) -> "HKStockFetcher":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        return None

    async def fetch_daily_kline(
        self,
        code: str,
        start_date: str,
        end_date: str,
        **kwargs,
    ) -> pd.DataFrame:
        """获取港股日K线行情。

        返回标准字段:
            time_key, open, high, low, close, volume, turnover, pe_ratio

        Args:
            code: 港股代码，支持: "00700", "hk.00700", "0700.HK" 等格式。
            start_date: 起始日期 "YYYY-MM-DD"。
            end_date: 截止日期 "YYYY-MM-DD"。
            **kwargs: 预留扩展。

        Returns:
            包含 OHLCV 等字段的原始 DataFrame（获取失败时返回空 DataFrame）。
        """
        pure_code = self._extract_pure_code(code)

        df = await self._run_sync_in_executor(
            self._query_akshare_kline_sync, pure_code, start_date, end_date
        )

        if df.empty:
            return df

        df = self._filter_low_liquidity(df)
        df = self._rename_fields(df)

        return df

    def _query_akshare_kline_sync(
        self, code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """通过 AkShare 同步查询港股日K线。

        Args:
            code: 纯 5 位港股代码。
            start_date: 起始日期。
            end_date: 截止日期。

        Returns:
            真实行情 DataFrame。

        Raises:
            RuntimeError: 网络不可达或查询失败。
        """
        import akshare as ak

        start = start_date.replace("-", "")
        end = end_date.replace("-", "")

        df = ak.stock_hk_hist(
            symbol=code,
            period="daily",
            start_date=start,
            end_date=end,
            adjust="",
        )

        if df.empty:
            logger.warning("港股 %s 查询返回空 (%s ~ %s)", code, start_date, end_date)
            return pd.DataFrame()

        col_map = {
            "日期": "date", "开盘": "open", "最高": "high",
            "最低": "low", "收盘": "close", "成交量": "volume",
            "成交额": "amount", "振幅": "amplitude", "涨跌幅": "pctChg",
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
        df["code"] = code
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"], errors="coerce")

        try:
            spot_df = ak.stock_hk_spot_em()
            if not spot_df.empty and "代码" in spot_df.columns and "市盈率" in spot_df.columns:
                row = spot_df[spot_df["代码"].astype(str).str.zfill(5) == code]
                if not row.empty:
                    pe = row["市盈率"].iloc[0]
                    if pd.notna(pe) and pe > 0:
                        df["peTTM"] = float(pe)
        except Exception:
            pass

        logger.info("港股 %s (AkShare) 获取成功，共 %d 条记录", code, len(df))
        return df

    def _extract_pure_code(self, code: str) -> str:
        """从各种输入格式中提取纯 5 位港股数字代码。"""
        code = code.strip()
        if code.lower().startswith("hk."):
            code = code[3:]
        if code.upper().endswith(".HK"):
            code = code[:-3]
        return code.zfill(5)

    def _filter_low_liquidity(self, df: pd.DataFrame) -> pd.DataFrame:
        """剔除日均成交额 < 100 万港元的非流动性标的。"""
        if df.empty or "amount" not in df.columns:
            return df
        if df["amount"].mean() < self.min_daily_turnover:
            code_tag = df["code"].iloc[0] if "code" in df.columns else "unknown"
            logger.warning("港股 %s 日均成交额过低，已剔除", code_tag)
            return pd.DataFrame()
        return df

    def _rename_fields(self, df: pd.DataFrame) -> pd.DataFrame:
        """字段名映射: date->time_key, amount->turnover, peTTM->pe_ratio"""
        if df.empty:
            return df
        rename_map = {"date": "time_key", "amount": "turnover", "peTTM": "pe_ratio"}
        actual = {k: v for k, v in rename_map.items() if k in df.columns}
        return df.rename(columns=actual)

    @staticmethod
    def get_board_lot(code: str) -> int:
        """获取港股整手交易单位（board lot）。"""
        BOARD_LOT_MAP = {
            "00700": 100, "00005": 400, "00388": 100, "01810": 200,
            "09988": 100, "09618": 100, "03690": 100, "09999": 100,
        }
        pure_code = code.strip()
        if pure_code.lower().startswith("hk."):
            pure_code = pure_code[3:]
        if pure_code.upper().endswith(".HK"):
            pure_code = pure_code[:-3]
        return BOARD_LOT_MAP.get(pure_code.zfill(5), 100)

    def _standardize_dataframe(self, df: pd.DataFrame, code: str) -> pd.DataFrame:
        """港股专用标准化：添加 HKD 货币标识。"""
        if df.empty:
            return df
        df = super()._standardize_dataframe(df, code)
        if "currency" not in df.columns:
            df["currency"] = "HKD"
        return df
