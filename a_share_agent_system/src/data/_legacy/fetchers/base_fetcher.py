"""
BaseFetcher 抽象基类
------------------
定义所有数据获取器的统一接口，提供：
1. 指数退避重试机制
2. 异步执行封装
3. SQLite 缓存管理
4. 统一的 DataFrame 输出规范

商用预留：Level-2 实时数据接口预留位（参见 _fetch_l2_data 方法文档）。
"""

import asyncio
import hashlib
import logging
import sqlite3
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd

# 配置模块级日志
logger = logging.getLogger(__name__)


class BaseFetcher(ABC):
    """金融数据获取器抽象基类。

    所有具体数据源（A股、港股等）必须继承此类并实现抽象方法。
    基类提供了重试、缓存、时区标准化等通用能力。

    Attributes:
        cache_dir: 本地缓存目录路径。
        max_retries: 最大重试次数（默认3次）。
        timezone: 统一时区，默认 Asia/Shanghai。
    """

    # 线程池，用于将同步IO操作转为异步
    _thread_pool: Optional[ThreadPoolExecutor] = None

    def __init__(
        self,
        cache_dir: str = "./data/processed",
        max_retries: int = 3,
        timezone: str = "Asia/Shanghai",
    ) -> None:
        """初始化 BaseFetcher。

        Args:
            cache_dir: SQLite 缓存文件存放目录。
            max_retries: 网络请求失败时的最大重试次数。
            timezone: 时间索引的目标时区。
        """
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_retries = max_retries
        self.timezone = timezone

        # 缓存数据库路径: cache/<类名>.db
        self._cache_db_path = self.cache_dir / f"{self.__class__.__name__}.db"

    # ------------------------------------------------------------------
    #  抽象方法：子类必须实现
    # ------------------------------------------------------------------

    @abstractmethod
    async def fetch_daily_kline(
        self,
        code: str,
        start_date: str,
        end_date: str,
        **kwargs,
    ) -> pd.DataFrame:
        """获取指定标的的日K线数据。

        Args:
            code: 股票代码（A股6位数字字符串，港股5位数字字符串）。
            start_date: 起始日期，格式 "YYYY-MM-DD"。
            end_date: 截止日期，格式 "YYYY-MM-DD"。
            **kwargs: 子类特有的可选参数（如复权方式）。

        Returns:
            包含OHLCV等行情字段的 DataFrame，时间索引已标准化。
        """
        ...

    # ------------------------------------------------------------------
    #  商用预留接口 —— Level-2 实时数据（Phase 2+ 启用）
    # ------------------------------------------------------------------

    async def fetch_l2_data(self, code: str) -> Optional[pd.DataFrame]:
        """预留 Level-2 实时行情数据接口。

        当前默认返回 None，子类可在接入实时数据源后覆写。
        商用场景中可对接交易所 L2 行情或 QMT xtquant 实时接口。

        Args:
            code: 股票代码。

        Returns:
            Level-2 行情 DataFrame（当前为 None）。
        """
        return None

    # ------------------------------------------------------------------
    #  【核心】指数退避重试机制
    #  当网络抖动或数据源限流时，按 3s -> 6s -> 12s 递增等待
    # ------------------------------------------------------------------

    async def _retry_with_backoff(self, coro_func, *args, **kwargs):
        """带指数退避的异步重试包装器。

        重试等待策略：base_delay * (2 ** (attempt - 1))，即 3s, 6s, 12s。

        Args:
            coro_func: 需要重试的协程函数。
            *args: 位置参数。
            **kwargs: 关键字参数。

        Returns:
            coro_func 的成功返回值。

        Raises:
            RuntimeError: 所有重试均失败时抛出，携带最后一次异常信息。
        """
        base_delay = 3.0  # 基础延迟秒数，适当加大以应对 API 限流
        last_error: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                logger.debug("第 %d/%d 次尝试...", attempt, self.max_retries)
                return await coro_func(*args, **kwargs)
            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    delay = base_delay * (2 ** (attempt - 1))
                    logger.warning(
                        "请求失败 (attempt %d/%d): %s，%d 秒后重试...",
                        attempt, self.max_retries, e, delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error("已达最大重试次数 %d，请求最终失败: %s", self.max_retries, e)

        raise RuntimeError(
            f"数据获取失败，已重试 {self.max_retries} 次，最后错误: {last_error}"
        )

    # ------------------------------------------------------------------
    #  异步封装：将同步方法放入线程池执行
    # ------------------------------------------------------------------

    async def _run_sync_in_executor(self, func, *args, **kwargs) -> pd.DataFrame:
        """在线程池中执行同步方法，避免阻塞事件循环。

        BaoStock 等库的 API 是同步阻塞的，通过此方法包装后即可在 async 上下文中使用。

        Args:
            func: 同步函数。
            *args: 位置参数。
            **kwargs: 关键字参数。

        Returns:
            func 的返回值（DataFrame）。
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._get_thread_pool(),
            lambda: func(*args, **kwargs),
        )

    @classmethod
    def _get_thread_pool(cls) -> ThreadPoolExecutor:
        """获取全局共享的线程池（懒加载单例模式）。

        Returns:
            ThreadPoolExecutor 实例。
        """
        if cls._thread_pool is None:
            cls._thread_pool = ThreadPoolExecutor(
                max_workers=4, thread_name_prefix="fetcher"
            )
        return cls._thread_pool

    # ------------------------------------------------------------------
    #  SQLite 缓存：避免重复请求历史数据
    # ------------------------------------------------------------------

    def _get_cache_key(self, code: str, start_date: str, end_date: str, **kwargs) -> str:
        """根据请求参数生成唯一的缓存键（SHA256 哈希）。

        Args:
            code: 股票代码。
            start_date: 起始日期。
            end_date: 截止日期。
            **kwargs: 额外参数（如复权方式）。

        Returns:
            64位十六进制哈希字符串。
        """
        raw = f"{code}|{start_date}|{end_date}|{sorted(kwargs.items())}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _read_cache(self, cache_key: str) -> Optional[pd.DataFrame]:
        """从 SQLite 缓存中读取已存储的行情数据。

        缓存表结构:
            cache_key TEXT PRIMARY KEY,
            data_blob BLOB,          -- DataFrame 的 Parquet 字节流
            created_at TEXT           -- ISO 时间戳

        Args:
            cache_key: 缓存键哈希值。

        Returns:
            缓存的 DataFrame，若不存在则返回 None。
        """
        try:
            conn = sqlite3.connect(str(self._cache_db_path))
            cursor = conn.execute(
                "SELECT data_blob FROM fetcher_cache WHERE cache_key = ?",
                (cache_key,),
            )
            row = cursor.fetchone()
            conn.close()
            if row is not None:
                import io
                buffer = io.BytesIO(row[0])
                df = pd.read_parquet(buffer)
                logger.info("命中缓存: %s...", cache_key[:16])
                return df
        except Exception:
            pass
        return None

    def _write_cache(self, cache_key: str, df: pd.DataFrame) -> None:
        """将 DataFrame 写入 SQLite 缓存。

        使用 Parquet 格式存储以节省空间并保留数据类型。

        Args:
            cache_key: 缓存键哈希值。
            df: 要缓存的 DataFrame。
        """
        try:
            conn = sqlite3.connect(str(self._cache_db_path))
            conn.execute(
                """CREATE TABLE IF NOT EXISTS fetcher_cache (
                    cache_key TEXT PRIMARY KEY,
                    data_blob BLOB,
                    created_at TEXT
                )"""
            )
            import io
            buffer = io.BytesIO()
            df.to_parquet(buffer, index=True)
            conn.execute(
                "INSERT OR REPLACE INTO fetcher_cache VALUES (?, ?, datetime('now'))",
                (cache_key, buffer.getvalue()),
            )
            conn.commit()
            conn.close()
            logger.info("写入缓存: %s...", cache_key[:16])
        except Exception as e:
            logger.warning("缓存写入失败: %s", e)

    # ------------------------------------------------------------------
    #  DataFrame 标准化处理
    # ------------------------------------------------------------------

    def _standardize_dataframe(self, df: pd.DataFrame, code: str) -> pd.DataFrame:
        """将原始行情 DataFrame 统一为标准格式。

        处理内容:
            1. 将时间列设置为索引并转为 datetime64[ns]
            2. 统一时区为 Asia/Shanghai
            3. 按时间升序排列
            4. 确保浮点列类型正确

        Args:
            df: 原始行情数据。
            code: 股票代码，用于日志追踪。

        Returns:
            标准化后的 DataFrame。
        """
        if df.empty:
            logger.warning("股票 %s 返回空数据", code)
            return df

        # 尝试自动识别时间列（date, time_key, trade_date 等常见命名）
        time_col_candidates = ["date", "time_key", "trade_date", "时间", "日期"]
        time_col = None
        for col in time_col_candidates:
            if col in df.columns:
                time_col = col
                break

        if time_col is None:
            logger.warning("股票 %s 未找到可识别的时间列，跳过标准化", code)
            return df

        df[time_col] = pd.to_datetime(df[time_col])
        df = df.set_index(time_col)

        if df.index.tz is None:
            df.index = df.index.tz_localize(self.timezone)
        else:
            df.index = df.index.tz_convert(self.timezone)

        df = df.sort_index()

        if "code" not in df.columns:
            df["code"] = code

        return df

    # ------------------------------------------------------------------
    #  通用流水线：缓存检查 -> 获取 -> 校验 -> 标准化 -> 缓存写入
    # ------------------------------------------------------------------

    async def get_data(
        self,
        code: str,
        start_date: str,
        end_date: str,
        **kwargs,
    ) -> pd.DataFrame:
        """通用数据获取流水线入口。

        流程:
            1. 检查 SQLite 缓存，命中则直接返回
            2. 通过指数退避重试机制从数据源获取原始数据
            3. 标准化 DataFrame（时区、索引）
            4. 写入缓存供后续使用

        Args:
            code: 股票代码。
            start_date: 起始日期 "YYYY-MM-DD"。
            end_date: 截止日期 "YYYY-MM-DD"。
            **kwargs: 传递给 fetch_daily_kline 的额外参数。

        Returns:
            标准化的行情 DataFrame。
        """
        cache_key = self._get_cache_key(code, start_date, end_date, **kwargs)

        # 步骤1：尝试读取缓存
        cached = self._read_cache(cache_key)
        if cached is not None:
            return cached

        # 步骤2：带重试的异步获取
        logger.info("获取 %s 数据: %s ~ %s", code, start_date, end_date)
        df = await self._retry_with_backoff(
            self.fetch_daily_kline, code, start_date, end_date, **kwargs
        )

        # 步骤3：标准化
        df = self._standardize_dataframe(df, code)

        # 步骤4：写入缓存
        if not df.empty:
            self._write_cache(cache_key, df)

        return df
