# -*- coding: utf-8 -*-
"""
market_store.py — DuckDB 行情/财务/新闻查询接口
================================================
面向 Agent 的统一数据访问层：屏蔽 DuckDB 细节，
提供行情区间、最新财务、财务历史、新闻检索等查询。

用法:
    from src.data.storage.market_store import MarketStore
    store = MarketStore()                      # 默认 ./data/processed/market_data.duckdb
    df = store.daily_range("sh.600519", "2026-01-01", "2026-08-20", adjust="hfq")
    df = store.latest_financial("sh.600519")
"""

import logging
import threading
from pathlib import Path
from typing import List, Optional

import duckdb
import pandas as pd

logger = logging.getLogger(__name__)

# 项目根目录（src/data/storage/market_store.py → 向上 3 级到 a_share_agent_system/）
ROOT = Path(__file__).resolve().parents[3]

DEFAULT_DB = ROOT / "data" / "processed" / "market_data.duckdb"

# 允许的日线表白名单（防注入）
_DAILY_TABLES = {"hfq": "daily_hfq", "raw": "daily_raw"}


class MarketStore:
    """DuckDB 统一查询接口（线程安全：每查询独立连接）。

    Attributes:
        db_path: DuckDB 数据库文件路径。
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        """初始化 MarketStore。

        Args:
            db_path: DuckDB 库文件路径，默认 ./data/processed/market_data.duckdb。
        """
        self.db_path = Path(db_path) if db_path else DEFAULT_DB
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"DuckDB 库不存在: {self.db_path}，请先运行 scripts/build_market_db.py"
            )
        self._lock = threading.Lock()
        logger.info("MarketStore 就绪: %s", self.db_path)

    # ------------------------------------------------------------------
    #  内部工具
    # ------------------------------------------------------------------

    def _connect(self) -> duckdb.DuckDBPyConnection:
        """建立只读连接（避免写锁与多线程冲突）。"""
        con = duckdb.connect(str(self.db_path), read_only=True)
        con.execute("SET memory_limit='8GB'")
        con.execute("SET threads=8")
        return con

    @staticmethod
    def _normalize_code(code: str) -> str:
        """将 6 位数字代码转为 sh./sz. 前缀形式（行情表）。

        Args:
            code: 如 "600519" 或 "sh.600519"。

        Returns:
            标准化代码，如 "sh.600519"。
        """
        c = code.strip().lower()
        if "." in c:
            return c
        prefix = "sh" if c.startswith(("5", "6", "9")) else "sz"
        return f"{prefix}.{c}"

    @staticmethod
    def _six_digit(code: str) -> str:
        """提取 6 位数字代码（财务表/新闻表格式）。

        Args:
            code: 如 "sh.600519" 或 "600519"。

        Returns:
            6 位数字代码，如 "600519"。
        """
        c = code.strip().lower()
        return c.split(".")[-1] if "." in c else c

    # ------------------------------------------------------------------
    #  行情查询
    # ------------------------------------------------------------------

    def daily_range(
        self,
        code: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        adjust: str = "hfq",
        fields: Optional[List[str]] = None,
    ) -> pd.DataFrame:
        """查询单只股票日线区间。

        Args:
            code: 股票代码（支持 600519 / sh.600519）。
            start: 起始日期 "YYYY-MM-DD"，None 表示最早。
            end: 结束日期 "YYYY-MM-DD"，None 表示最新。
            adjust: "hfq" 前复权 / "raw" 不复权。
            fields: 返回字段列表，None 返回全部。

        Returns:
            按日期升序的 DataFrame。
        """
        table = _DAILY_TABLES.get(adjust)
        if table is None:
            raise ValueError(f"adjust 必须为 hfq/raw, 收到 {adjust}")

        norm = self._normalize_code(code)
        cols = ", ".join(fields) if fields else "*"
        sql = f"SELECT {cols} FROM {table} WHERE code = ?"
        params: list = [norm]

        if start:
            sql += " AND date >= CAST(? AS DATE)"
            params.append(start)
        if end:
            sql += " AND date <= CAST(? AS DATE)"
            params.append(end)
        sql += " ORDER BY date"

        with self._lock:
            con = self._connect()
            try:
                df = con.execute(sql, params).fetchdf()
            finally:
                con.close()
        return df

    def daily_latest(self, code: str, adjust: str = "hfq", n: int = 1) -> pd.DataFrame:
        """查询最近 n 个交易日行情（用于复权校验/实时对齐）。

        Args:
            code: 股票代码。
            adjust: "hfq" / "raw"。
            n: 返回最近交易日数量。

        Returns:
            按日期降序的最近 n 行。
        """
        table = _DAILY_TABLES.get(adjust)
        if table is None:
            raise ValueError(f"adjust 必须为 hfq/raw, 收到 {adjust}")
        norm = self._normalize_code(code)
        sql = (
            f"SELECT * FROM {table} WHERE code = ? "
            "ORDER BY date DESC LIMIT ?"
        )
        with self._lock:
            con = self._connect()
            try:
                return con.execute(sql, [norm, n]).fetchdf()
            finally:
                con.close()

    # ------------------------------------------------------------------
    #  财务查询
    # ------------------------------------------------------------------

    def latest_financial(self, code: str, as_of: Optional[str] = None) -> pd.DataFrame:
        """查询最近一期业绩报表（Point-in-Time 对齐）。

        Args:
            code: 股票代码。
            as_of: 对齐日期 "YYYY-MM-DD"，仅返回 pub_date 早于该日期的报表，
                   None 返回全部中最近一期。

        Returns:
            最近一期报表（0 或 1 行）。
        """
        norm = self._six_digit(code)
        if as_of:
            sql = (
                "SELECT * FROM financials_yjbb "
                "WHERE code = ? AND pub_date <= CAST(? AS DATE) "
                "ORDER BY report_date DESC LIMIT 1"
            )
            params: list = [norm, as_of]
        else:
            sql = (
                "SELECT * FROM financials_yjbb "
                "WHERE code = ? ORDER BY report_date DESC LIMIT 1"
            )
            params = [norm]

        with self._lock:
            con = self._connect()
            try:
                return con.execute(sql, params).fetchdf()
            finally:
                con.close()

    def financial_history(self, code: str, n: int = 8) -> pd.DataFrame:
        """查询最近 n 期财务（按报告期降序）。

        Args:
            code: 股票代码。
            n: 期数。

        Returns:
            按 report_date 降序的 DataFrame。
        """
        norm = self._six_digit(code)
        sql = (
            "SELECT * FROM financials_yjbb WHERE code = ? "
            "ORDER BY report_date DESC LIMIT ?"
        )
        with self._lock:
            con = self._connect()
            try:
                return con.execute(sql, [norm, n]).fetchdf()
            finally:
                con.close()

    # ------------------------------------------------------------------
    #  新闻查询（结构化字段过滤）
    # ------------------------------------------------------------------

    def news_by_stock(
        self,
        code: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 20,
    ) -> pd.DataFrame:
        """按股票代码 + 时间范围查询新闻（结构化检索）。

        Args:
            code: 股票代码（6 位数字，新闻表无 sh./sz. 前缀）。
            start: 起始时间 "YYYY-MM-DD"。
            end: 结束时间。
            limit: 返回条数。

        Returns:
            按发布时间降序的新闻。
        """
        six_digit = code.strip().lower().split(".")[-1]
        sql = (
            "SELECT title, text, source, publish_time, url FROM news_articles "
            "WHERE stock_code = ?"
        )
        params: list = [six_digit]
        if start:
            sql += " AND publish_time >= CAST(? AS TIMESTAMP)"
            params.append(start)
        if end:
            sql += " AND publish_time <= CAST(? AS TIMESTAMP)"
            params.append(end)
        sql += " ORDER BY publish_time DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            con = self._connect()
            try:
                return con.execute(sql, params).fetchdf()
            finally:
                con.close()

    # ------------------------------------------------------------------
    #  元信息
    # ------------------------------------------------------------------

    def table_stats(self) -> pd.DataFrame:
        """返回各表行数与表结构概览。"""
        sql = (
            "SELECT table_name, "
            "(SELECT COUNT(*) FROM information_schema.tables t2 WHERE t2.table_name = t.table_name) "
            "FROM information_schema.tables t"
        )
        # 简化：直接对各已知表 count
        rows = []
        with self._lock:
            con = self._connect()
            try:
                for t in ("daily_hfq", "daily_raw", "financials_yjbb", "news_articles"):
                    try:
                        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                        rows.append({"table": t, "rows": n})
                    except Exception:
                        rows.append({"table": t, "rows": -1})
                return pd.DataFrame(rows)
            finally:
                con.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    store = MarketStore()
    print(store.table_stats().to_string(index=False))
