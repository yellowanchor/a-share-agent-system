"""
AShareFetcher — A股数据获取器
-----------------------------
基于 BaoStock 获取 A 股日K线行情数据。
支持前复权/后复权/不复权，自动过滤 ST/*ST 股票。
"""

import asyncio
import logging
from typing import List, Optional

import pandas as pd

from src.data.fetchers.base_fetcher import BaseFetcher

logger = logging.getLogger(__name__)


class AShareFetcher(BaseFetcher):
    """A股日K线行情获取器。

    基于 BaoStock (baostock) 开源证券数据 API 实现。
    自动处理登录/登出、ST 过滤、复权等业务逻辑。

    Attributes:
        frequency: K线周期，默认 "d"（日线）。
        adjustment: 复权方式，默认 "2"（前复权）。

    Typical usage:
        async with AShareFetcher() as fetcher:
            df = await fetcher.get_data("sh.600519", "2024-01-01", "2024-12-31")
    """

    # BaoStock 复权方式映射
    #   "1": 后复权, "2": 前复权, "3": 不复权
    ADJUST_MAP = {"qfq": "2", "hfq": "1", "bfq": "3"}

    ST_PATTERN = "ST"

    def __init__(
        self,
        frequency: str = "d",
        adjustment: str = "2",
        cache_dir: str = "./data/processed",
        max_retries: int = 3,
    ) -> None:
        """初始化 AShareFetcher。

        Args:
            frequency: K线周期，"d"=日线, "w"=周线, "m"=月线,
                       "5"/"15"/"30"/"60"=分钟线。
            adjustment: 复权方式，"1"=后复权, "2"=前复权(默认), "3"=不复权。
            cache_dir: 缓存目录。
            max_retries: 最大重试次数。
        """
        super().__init__(cache_dir=cache_dir, max_retries=max_retries)
        self.frequency = frequency
        self.adjustment = adjustment

        # BaoStock 会话状态
        self._bs: Optional[object] = None
        self._logged_in: bool = False

    # ------------------------------------------------------------------
    #  上下文管理器：自动管理 BaoStock 登录/登出
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "AShareFetcher":
        """异步上下文管理器入口，自动执行 BaoStock 登录。"""
        await self.login()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """异步上下文管理器出口，自动执行 BaoStock 登出。"""
        await self.logout()
        return None

    async def login(self) -> None:
        """登录 BaoStock 数据服务。"""
        if self._logged_in:
            return
        await self._run_sync_in_executor(self._sync_login)

    async def logout(self) -> None:
        """登出 BaoStock 数据服务。"""
        if not self._logged_in:
            return
        await self._run_sync_in_executor(self._sync_logout)

    def _sync_login(self) -> None:
        """同步登录 BaoStock（在线程池中执行）。"""
        import baostock as bs
        self._bs = bs
        lg = bs.login()
        if lg.error_code != "0":
            raise RuntimeError(f"BaoStock 登录失败: {lg.error_msg}")
        self._logged_in = True
        logger.info("BaoStock 登录成功")

    def _sync_logout(self) -> None:
        """同步登出 BaoStock（在线程池中执行）。"""
        if self._bs is not None:
            self._bs.logout()
            self._logged_in = False
            logger.info("BaoStock 已登出")

    # ------------------------------------------------------------------
    #  核心数据获取
    # ------------------------------------------------------------------

    async def fetch_daily_kline(
        self,
        code: str,
        start_date: str,
        end_date: str,
        **kwargs,
    ) -> pd.DataFrame:
        """获取 A 股日K线行情（带前复权）。

        返回标准字段:
            date, code, open, high, low, close, preclose, volume,
            amount, adjustflag, turn, tradestatus, pctChg, isST

        Args:
            code: A股代码，格式 "sh.600519" 或 "sz.000001"。
            start_date: 起始日期 "YYYY-MM-DD"。
            end_date: 截止日期 "YYYY-MM-DD"。
            **kwargs:
                frequency: 覆盖默认K线周期。
                adjustment: 覆盖默认复权方式。

        Returns:
            包含 OHLCV 等字段的原始 DataFrame（尚未标准化）。
        """
        code = self._normalize_code(code)
        frequency = kwargs.get("frequency", self.frequency)
        adjustment = kwargs.get("adjustment", self.adjustment)
        if adjustment in self.ADJUST_MAP:
            adjustment = self.ADJUST_MAP[adjustment]

        df = await self._run_sync_in_executor(
            self._query_kline_sync, code, start_date, end_date, frequency, adjustment
        )

        df = self._filter_st_stocks(df)
        return df

    def _normalize_code(self, code: str) -> str:
        """将用户输入的股票代码标准化为 BaoStock 格式。

        规则:
            - 若已含 "sh." / "sz." 前缀则直接返回
            - 纯6位数字：60xxxx -> sh.60xxxx，其他 -> sz.XXXXXX

        Args:
            code: 用户输入的代码字符串。

        Returns:
            BaoStock 标准格式代码。
        """
        code = code.strip()
        if code.startswith(("sh.", "sz.", "bj.")):
            return code
        if code.isdigit() and len(code) == 6:
            if code.startswith(("60", "68")):
                return f"sh.{code}"
            else:
                return f"sz.{code}"
        return code

    def _query_kline_sync(
        self,
        code: str,
        start_date: str,
        end_date: str,
        frequency: str,
        adjustment: str,
    ) -> pd.DataFrame:
        """同步查询日K线（在线程池中执行）。

        Args:
            code: BaoStock 格式股票代码。
            start_date: 起始日期。
            end_date: 截止日期。
            frequency: K线周期。
            adjustment: 复权方式编码。

        Returns:
            原始行情 DataFrame。

        Raises:
            RuntimeError: BaoStock 查询失败。
        """
        import baostock as bs

        fields = (
            "date,code,open,high,low,close,preclose,volume,"
            "amount,adjustflag,turn,tradestatus,pctChg,isST"
        )

        rs = bs.query_history_k_data_plus(
            code,
            fields,
            start_date=start_date,
            end_date=end_date,
            frequency=frequency,
            adjustflag=adjustment,
        )

        if rs.error_code != "0":
            raise RuntimeError(
                f"BaoStock 查询失败: code={code}, error={rs.error_msg}"
            )

        data_list = []
        while (rs.error_code == "0") and rs.next():
            data_list.append(rs.get_row_data())

        if not data_list:
            logger.warning("查询 %s 返回空数据 (%s ~ %s)", code, start_date, end_date)
            return pd.DataFrame()

        df = pd.DataFrame(data_list, columns=rs.fields)

        numeric_cols = [
            "open", "high", "low", "close", "preclose",
            "volume", "amount", "turn", "pctChg",
        ]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        return df

    def _filter_st_stocks(self, df: pd.DataFrame) -> pd.DataFrame:
        """过滤 ST/*ST 等风险警示股票。

        Args:
            df: 原始行情数据。

        Returns:
            过滤后的 DataFrame。
        """
        if df.empty:
            return df

        if "isST" in df.columns:
            df["isST"] = pd.to_numeric(df["isST"], errors="coerce").fillna(0).astype(int)
            st_count = (df["isST"] == 1).sum()
            if st_count > 0:
                logger.info("过滤掉 %d 条 ST/*ST 股票记录", st_count)
                df = df[df["isST"] != 1].copy()

        return df

    # ------------------------------------------------------------------
    #  便捷方法：批量获取多只股票数据
    # ------------------------------------------------------------------

    async def fetch_multiple(
        self,
        codes: List[str],
        start_date: str,
        end_date: str,
        **kwargs,
    ) -> pd.DataFrame:
        """并发获取多只 A 股的日K线数据。

        Args:
            codes: 股票代码列表。
            start_date: 起始日期。
            end_date: 截止日期。
            **kwargs: 传递给 fetch_daily_kline 的额外参数。

        Returns:
            合并后的 DataFrame，按 (date, code) 多级索引排列。
        """
        tasks = [
            self.get_data(code, start_date, end_date, **kwargs)
            for code in codes
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        valid_dfs = []
        for code, r in zip(codes, results):
            if isinstance(r, Exception):
                logger.error("股票 %s 获取失败: %s", code, r)
            elif not r.empty:
                valid_dfs.append(r)

        if not valid_dfs:
            return pd.DataFrame()

        combined = pd.concat(valid_dfs)
        return combined.sort_index()
