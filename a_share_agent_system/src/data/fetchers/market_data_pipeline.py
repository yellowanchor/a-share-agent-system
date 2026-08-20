"""
MarketDataPipeline — 全A股行情数据流水线
-----------------------------------------
实现 Phase 1 数据基础建设的核心流水线:
    1. 获取沪深300成分股列表
    2. 批量获取近3年日K线 + 财务数据
    3. 输出 Parquet 文件并载入 DuckDB 内存模式
    4. 生成数据质量校验报告
"""

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import duckdb
import pandas as pd
import tqdm
from tqdm.asyncio import tqdm_asyncio

from src.data.fetchers.a_share_fetcher import AShareFetcher
from src.data.validators.data_validator import DataValidator

logger = logging.getLogger(__name__)


class MarketDataPipeline:
    """全A股行情数据流水线。

    负责沪深300成分股的批量行情获取、财务数据拉取、
    Parquet 落盘和 DuckDB 载入。

    Attributes:
        output_dir: Parquet/DuckDB 输出目录。
        n_years: 获取历史数据的年数（默认3年）。
        batch_size: 并发获取的股票数量上限。
    """

    def __init__(
        self,
        output_dir: str = "./data/processed",
        n_years: int = 3,
        batch_size: int = 10,
    ) -> None:
        """初始化数据流水线。

        Args:
            output_dir: Parquet/DuckDB 输出目录。
            n_years: 历史数据年数。
            batch_size: 并发获取上限（避免 API 限流）。
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.n_years = n_years
        self.batch_size = batch_size

        # DuckDB 连接（内存模式，高性能查询）
        self._db_path = str(self.output_dir / "market_data.duckdb")
        self.con: Optional[duckdb.DuckDBPyConnection] = None

    # ------------------------------------------------------------------
    #  DuckDB 生命周期
    # ------------------------------------------------------------------

    def open_db(self) -> duckdb.DuckDBPyConnection:
        """打开 DuckDB 连接（首次使用内存模式，后续可持久化）。

        DuckDB 内存模式下查询速度极快，适合策略回测场景。
        也创建持久化文件用于长期存储。

        Returns:
            DuckDB 连接对象。
        """
        # 内存模式 + 持久化双开
        self.con = duckdb.connect(self._db_path)
        logger.info("DuckDB 已连接 (持久化: %s)", self._db_path)
        return self.con

    def close_db(self) -> None:
        """关闭 DuckDB 连接。"""
        if self.con is not None:
            self.con.close()
            self.con = None
            logger.info("DuckDB 已关闭")

    # ------------------------------------------------------------------
    #  沪深300成分股获取
    # ------------------------------------------------------------------

    @staticmethod
    def get_hs300_constituents() -> pd.DataFrame:
        """获取沪深300指数最新成分股列表。

        通过 BaoStock 的 query_sz50_stocks / query_hs300_stocks 接口获取。

        Returns:
            成分股 DataFrame，包含 code (BaoStock格式) 和 code_name 列。

        Raises:
            RuntimeError: BaoStock 查询失败。
        """
        import baostock as bs

        lg = bs.login()
        if lg.error_code != "0":
            raise RuntimeError(f"BaoStock 登录失败: {lg.error_msg}")

        try:
            # 获取沪深300成分股
            rs = bs.query_hs300_stocks()
            if rs.error_code != "0":
                raise RuntimeError(f"沪深300成分股查询失败: {rs.error_msg}")

            data = []
            while (rs.error_code == "0") and rs.next():
                data.append(rs.get_row_data())

            df = pd.DataFrame(data, columns=rs.fields)
            # 标准化代码格式: sh.XXXXXX / sz.XXXXXX
            # BaoStock query_hs300_stocks 返回的 code 可能已带 sh./sz. 前缀
            df["code"] = df["code"].apply(_normalize_bs_code)
            logger.info("沪深300成分股获取成功，共 %d 只", len(df))
            return df
        finally:
            bs.logout()

    # ------------------------------------------------------------------
    #  日K线批量获取
    # ------------------------------------------------------------------

    async def fetch_all_kline(
        self,
        codes: Optional[List[str]] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        max_stocks: Optional[int] = None,
    ) -> pd.DataFrame:
        """批量获取全A股日K线数据并合并。

        流程:
            1. 若未提供 codes，自动获取沪深300成分股
            2. 分批并发获取，避免 API 限流
            3. 合并所有股票数据为单一大 DataFrame

        Args:
            codes: 股票代码列表（None 则自动获取沪深300）。
            start_date: 起始日期（None 则取 n_years 年前）。
            end_date: 截止日期（None 则取今天）。
            max_stocks: 最大获取股票数（None 则不限，用于调试）。

        Returns:
            包含所有股票日K线的 DataFrame，索引为 (date, code)。
        """
        # 获取股票列表
        if codes is None:
            constituents = self.get_hs300_constituents()
            codes = constituents["code"].tolist()
            logger.info("使用沪深300成分股，共 %d 只", len(codes))

        if max_stocks is not None:
            codes = codes[:max_stocks]
            logger.info("限制为前 %d 只股票测试", max_stocks)

        # 日期范围
        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")
        if start_date is None:
            start_date = (
                datetime.now() - timedelta(days=365 * self.n_years)
            ).strftime("%Y-%m-%d")

        logger.info(
            "开始批量获取 %d 只股票日K线 (%s ~ %s)",
            len(codes), start_date, end_date,
        )

        # 分批并发获取
        all_dfs = []
        failed_codes = []

        for i in range(0, len(codes), self.batch_size):
            batch = codes[i:i + self.batch_size]
            async with AShareFetcher(cache_dir=str(self.output_dir)) as fetcher:
                tasks = [
                    fetcher.get_data(code, start_date, end_date)
                    for code in batch
                ]
                results = []
                for coro in tqdm_asyncio(tasks, desc=f"Batch {i // self.batch_size + 1}"):
                    try:
                        r = await coro
                    except Exception as exc:
                        r = exc
                    results.append(r)

                for code, result in zip(batch, results):
                    if isinstance(result, Exception):
                        logger.warning("股票 %s 获取失败: %s", code, result)
                        failed_codes.append(code)
                    elif not result.empty:
                        all_dfs.append(result)

        if not all_dfs:
            logger.warning("无任何股票数据获取成功")
            return pd.DataFrame()

        # 合并
        combined = pd.concat(all_dfs)
        # 对多股票数据按日期+代码排序
        if "code" in combined.columns:
            combined = combined.sort_index()
            combined = combined.reset_index().set_index(["date", "code"]).sort_index()

        logger.info(
            "批量获取完成: 成功 %d 只, 失败 %d 只, 总记录 %d 条",
            len(all_dfs), len(failed_codes), len(combined),
        )

        return combined

    # ------------------------------------------------------------------
    #  财务数据获取
    # ------------------------------------------------------------------

    @staticmethod
    def fetch_financials(
        codes: List[str],
        years: Optional[List[int]] = None,
    ) -> Dict[str, pd.DataFrame]:
        """获取指定股票的财务数据（资产负债表、利润表、现金流量表）。

        Args:
            codes: 股票代码列表（BaoStock 格式）。
            years: 年份列表，None 则取最近3年。

        Returns:
            {"balance": DataFrame, "income": DataFrame, "cashflow": DataFrame}
            三个表，统一含 code + year + quarter 字段。
        """
        import baostock as bs

        if years is None:
            current_year = datetime.now().year
            years = list(range(current_year - 3, current_year + 1))

        lg = bs.login()
        if lg.error_code != "0":
            raise RuntimeError(f"BaoStock 登录失败: {lg.error_msg}")

        try:
            balance_list = []
            income_list = []
            cashflow_list = []

            for code in tqdm.tqdm(codes, desc="财务数据"):
                for year in years:
                    for quarter in range(1, 5):
                        try:
                            # 资产负债表
                            rs_balance = bs.query_balance_data(code, year, quarter)
                            _append_financial_rows(rs_balance, balance_list, code, year, quarter)

                            # 利润表
                            rs_income = bs.query_profit_data(code, year, quarter)
                            _append_financial_rows(rs_income, income_list, code, year, quarter)

                            # 现金流量表
                            rs_cashflow = bs.query_cash_flow_data(code, year, quarter)
                            _append_financial_rows(rs_cashflow, cashflow_list, code, year, quarter)
                        except Exception:
                            continue

            results = {}
            if balance_list:
                results["balance"] = pd.DataFrame(balance_list)
            if income_list:
                results["income"] = pd.DataFrame(income_list)
            if cashflow_list:
                results["cashflow"] = pd.DataFrame(cashflow_list)

            logger.info(
                "财务数据获取完成: 资产负债表 %d 条, 利润表 %d 条, 现金流量表 %d 条",
                len(balance_list), len(income_list), len(cashflow_list),
            )
            return results

        finally:
            bs.logout()

    # ------------------------------------------------------------------
    #  Parquet 输出
    # ------------------------------------------------------------------

    def to_parquet(self, df: pd.DataFrame, name: str) -> str:
        """将 DataFrame 写入 Parquet 文件。

        Parquet 格式优点:
            - 列式存储，压缩率高（通常比CSV节省70-90%空间）
            - 保留数据类型（int/float/datetime），无需二次转换
            - 支持谓词下推，DuckDB查询时可跳过无关列

        Args:
            df: 要写入的 DataFrame。
            name: 文件名前缀（不含扩展名）。

        Returns:
            输出的 Parquet 文件路径。
        """
        path = str(self.output_dir / f"{name}.parquet")
        df.to_parquet(path, compression="snappy")
        size_mb = Path(path).stat().st_size / (1024 * 1024)
        logger.info("Parquet 写入完成: %s (%.1f MB)", path, size_mb)
        return path

    # ------------------------------------------------------------------
    #  DuckDB 载入
    # ------------------------------------------------------------------

    def load_to_duckdb(
        self,
        kline_df: Optional[pd.DataFrame] = None,
        financials: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> None:
        """将行情和财务数据载入 DuckDB 内存模式。

        创建的表:
            - kline_daily: 日K线行情
            - financial_balance: 资产负债表
            - financial_income: 利润表
            - financial_cashflow: 现金流量表

        Args:
            kline_df: 日K线 DataFrame。
            financials: 财务数据字典。
        """
        if self.con is None:
            self.open_db()

        # 日K线
        if kline_df is not None and not kline_df.empty:
            # 重置索引以便 DuckDB 查询
            df_flat = kline_df.reset_index()
            self.con.execute("DROP TABLE IF EXISTS kline_daily")
            self.con.execute("CREATE TABLE kline_daily AS SELECT * FROM df_flat")
            count = self.con.execute("SELECT COUNT(*) FROM kline_daily").fetchone()[0]
            logger.info("DuckDB 载入日K线: %d 条记录", count)

        # 财务数据
        table_names = {
            "balance": "financial_balance",
            "income": "financial_income",
            "cashflow": "financial_cashflow",
        }
        if financials:
            for key, table_name in table_names.items():
                if key in financials and not financials[key].empty:
                    self.con.execute(f"DROP TABLE IF EXISTS {table_name}")
                    df = financials[key]
                    self.con.execute(f"CREATE TABLE {table_name} AS SELECT * FROM df")
                    count = self.con.execute(
                        f"SELECT COUNT(*) FROM {table_name}"
                    ).fetchone()[0]
                    logger.info("DuckDB 载入 %s: %d 条记录", table_name, count)


def _normalize_bs_code(code: str) -> str:
    """标准化 BaoStock 返回的股票代码。

    处理 query_hs300_stocks() 等接口可能返回的各种格式:
        - "sh.600000"  -> "sh.600000"  (已有前缀，保持不变)
        - "600000"     -> "sh.600000"  (无前缀，按规则补全)
        - "000001"     -> "sz.000001"

    Args:
        code: BaoStock 返回的原始代码。

    Returns:
        "sh.XXXXXX" 或 "sz.XXXXXX" 格式。
    """
    code = code.strip()
    # 已含完整前缀，直接返回
    if code.startswith(("sh.", "sz.", "bj.")):
        return code
    # 纯数字：按上交所/深交所规则补前缀
    if code.startswith(("60", "68")):
        return f"sh.{code}"
    else:
        return f"sz.{code}"


def _append_financial_rows(
    rs, data_list: list, code: str, year: int, quarter: int
) -> None:
    """将 BaoStock 财务报表查询结果追加到列表。

    Args:
        rs: BaoStock 查询结果对象。
        data_list: 目标列表。
        code: 股票代码。
        year: 财年。
        quarter: 季度(1-4)。
    """
    if rs.error_code != "0":
        return
    while (rs.error_code == "0") and rs.next():
        row = dict(zip(rs.fields, rs.get_row_data()))
        row["code"] = code
        row["year"] = year
        row["quarter"] = quarter
        # 数值列转换
        for k, v in row.items():
            if k not in ("code", "year", "quarter", "pubDate", "statDate"):
                try:
                    row[k] = float(v) if v else None
                except (ValueError, TypeError):
                    pass
        data_list.append(row)


# ------------------------------------------------------------------
#  便捷运行入口
# ------------------------------------------------------------------

async def run_pipeline(
    output_dir: str = "./data/processed",
    n_years: int = 3,
    max_stocks: Optional[int] = None,
    with_financials: bool = False,
) -> dict:
    """执行完整的数据流水线（一键运行）。

    流程:
        1. 获取沪深300成分股
        2. 批量获取日K线 -> Parquet -> DuckDB
        3. (可选) 获取财务数据 -> DuckDB
        4. 生成数据质量校验报告

    Args:
        output_dir: 输出目录。
        n_years: 历史数据年数。
        max_stocks: 调试模式下限制股票数（None=全部300只）。
        with_financials: 是否获取财务数据（耗时较长）。

    Returns:
        包含各阶段结果的字典。
    """
    pipeline = MarketDataPipeline(
        output_dir=output_dir,
        n_years=n_years,
        batch_size=10,
    )

    # 步骤1: 获取日K线
    logger.info("=" * 50)
    logger.info("Phase 1 流水线启动: 日K线数据获取")
    logger.info("=" * 50)

    kline_df = await pipeline.fetch_all_kline(max_stocks=max_stocks)

    if kline_df.empty:
        logger.error("日K线获取失败，流水线终止")
        return {"status": "failed", "reason": "日K线获取失败"}

    # 步骤2: Parquet 输出
    parquet_path = pipeline.to_parquet(kline_df, "hs300_daily_kline")

    # 步骤3: DuckDB 载入
    pipeline.open_db()
    pipeline.load_to_duckdb(kline_df=kline_df)

    # 步骤4: (可选) 财务数据
    if with_financials:
        logger.info("=" * 50)
        logger.info("Phase 1 流水线: 财务数据获取")
        logger.info("=" * 50)

        codes = kline_df.index.get_level_values("code").unique().tolist() \
            if isinstance(kline_df.index, pd.MultiIndex) \
            else kline_df["code"].unique().tolist()

        financials = pipeline.fetch_financials(codes)
        pipeline.load_to_duckdb(financials=financials)

    # 步骤5: 校验报告
    validator = DataValidator()
    if isinstance(kline_df.index, pd.MultiIndex):
        # 多股票数据：每只股票单独校验
        codes = kline_df.index.get_level_values("code").unique()
        data_dict = {c: kline_df.xs(c, level="code") for c in codes[:5]}  # 抽样5只
        result = validator.validate_batch(data_dict)
    else:
        # 单股票
        validator.validate_market_data(kline_df, "unknown")

    pipeline.close_db()

    return {
        "status": "success",
        "parquet_path": parquet_path,
        "total_records": len(kline_df),
        "db_path": pipeline._db_path,
    }
