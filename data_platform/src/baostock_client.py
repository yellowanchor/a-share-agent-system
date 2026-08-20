# -*- coding: utf-8 -*-
"""
BaoStock 数据源封装
===================
BaoStock 是国内免费、无积分门槛的 A 股历史数据接口（底层为本地数据库）。
特点：
  - 登录为全局单例，**不支持并发**（线程不安全），必须顺序调用
  - 数据历史可回溯到 1990 年代，含退市股票历史
  - 提供后复权/前复权/不复权三套价格

封装原则：
  - 单进程内登录一次、复用到底，脚本结束时统一 logout
  - 每次查询带重试（BaoStock 偶发网络/服务异常）
  - 统一把返回的 DataFrame 转为规范列名（英文小写）
"""
from __future__ import annotations

import logging
import time

import pandas as pd

logger = logging.getLogger(__name__)

# 日线可用的字段清单（BaoStock 文档）
DAILY_FIELDS = (
    "date,code,open,high,low,close,preclose,volume,amount,"
    "adjustflag,turn,tradestatus,pctChg,peTTM,pbMRQ,psTTM,"
    "pcfNcfTTM,isST"
)

# 字段中文名 → 规范英文列名
COLUMN_MAP = {
    "date": "date",
    "code": "code",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "preclose": "preclose",
    "volume": "volume",          # 成交量（股）
    "amount": "amount",          # 成交额（元）
    "adjustflag": "adjustflag",  # 复权类型 1后 2前 3不复权
    "turn": "turn",              # 换手率（%）
    "tradestatus": "tradestatus",  # 1正常 0停牌
    "pctChg": "pct_chg",         # 涨跌幅（%）
    "peTTM": "pe_ttm",           # 市盈率（滚动）
    "pbMRQ": "pb_mrq",           # 市净率
    "psTTM": "ps_ttm",           # 市销率（滚动）
    "pcfNcfTTM": "pcf_ncf_ttm",  # 市现率（滚动）
    "isST": "is_st",             # 1 ST 0 非ST
    "code_name": "name",
    "tradeStatus": "trade_status",
}


class BaoStockClient:
    """BaoStock 客户端（单例登录，顺序调用）"""

    def __init__(self, retry: int = 3, retry_wait: float = 1.0):
        self._logged_in = False
        self.retry = retry
        self.retry_wait = retry_wait

    # ------------------------------------------------------------------
    # 登录 / 登出
    # ------------------------------------------------------------------
    def login(self) -> None:
        """登录（幂等：已登录则跳过）"""
        if self._logged_in:
            return
        import baostock as bs

        lg = bs.login()
        if lg.error_code != "0":
            raise ConnectionError(
                f"BaoStock 登录失败: {lg.error_code} {lg.error_msg}"
            )
        self._logged_in = True
        logger.info("BaoStock 登录成功")

    def logout(self) -> None:
        """登出（幂等）"""
        if not self._logged_in:
            return
        import baostock as bs

        bs.logout()
        self._logged_in = False
        logger.info("BaoStock 已登出")

    def __enter__(self):
        self.login()
        return self

    def __exit__(self, *exc):
        self.logout()

    # ------------------------------------------------------------------
    # 通用查询（带重试）
    # ------------------------------------------------------------------
    def _query(self, fn, *args, **kwargs) -> pd.DataFrame:
        """执行查询并转 DataFrame，失败自动重试"""
        import baostock as bs

        last_err = None
        for i in range(self.retry):
            try:
                rs = fn(*args, **kwargs)
                if rs.error_code != "0":
                    raise RuntimeError(
                        f"{rs.error_code} {rs.error_msg}"
                    )
                rows = []
                while rs.next():
                    rows.append(rs.get_row_data())
                if not rows:
                    return pd.DataFrame()
                df = pd.DataFrame(rows, columns=rs.fields)
                df = df.rename(columns=COLUMN_MAP)
                return df
            except Exception as e:  # noqa: BLE001
                last_err = e
                wait = self.retry_wait * (2 ** i)
                logger.warning(
                    "查询失败(第%d次): %s，%s 后重试", i + 1, e, wait
                )
                time.sleep(wait)
        raise RuntimeError(f"BaoStock 查询重试耗尽: {last_err}")

    # ------------------------------------------------------------------
    # 股票列表（含退市股策略）
    # ------------------------------------------------------------------
    def get_all_stock(self, day: str) -> pd.DataFrame:
        """
        获取指定日期在交易的股票列表（含当日停牌股）。
        返回列: code, trade_status, name

        退市股获取策略：用【历史早日期】+【最近交易日】两次列表取并集，
        即可覆盖"期间曾上市的全部A股"（含中间退市的）。
        """
        self.login()
        df = self._query(lambda: __import__("baostock").query_all_stock(day))
        if df.empty:
            return df
        return df[["code", "trade_status", "name"]]

    # ------------------------------------------------------------------
    # 交易日历
    # ------------------------------------------------------------------
    def get_trade_dates(self, start: str, end: str) -> pd.DataFrame:
        """获取交易日历。返回列: calendar_date, is_trading_day"""
        self.login()

        def _q():
            import baostock as bs
            return bs.query_trade_dates(start_date=start, end_date=end)

        df = self._query(_q)
        return df[["calendar_date", "is_trading_day"]]

    # ------------------------------------------------------------------
    # 日线行情
    # ------------------------------------------------------------------
    def get_daily(self, code: str, start: str, end: str,
                  adjust: int = 1) -> pd.DataFrame:
        """
        获取单只股票日线行情。
        adjust: 1=后复权(hfq)  2=前复权(qfq)  3=不复权(原始价)
        返回列: date, code, open, high, low, close, preclose, volume,
                amount, turn, tradestatus, pct_chg, pe_ttm, pb_mrq,
                ps_ttm, pcf_ncf_ttm, is_st
        """
        self.login()

        def _q():
            import baostock as bs
            return bs.query_history_k_data_plus(
                code,
                DAILY_FIELDS,
                start_date=start,
                end_date=end,
                frequency="d",
                adjustflag=str(adjust),
            )

        df = self._query(_q)
        if df.empty:
            return df
        # 类型转换（BaoStock 返回全字符串）
        float_cols = ["open", "high", "low", "close", "preclose",
                      "volume", "amount", "turn", "pct_chg",
                      "pe_ttm", "pb_mrq", "ps_ttm", "pcf_ncf_ttm"]
        for c in float_cols:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["is_st"] = pd.to_numeric(df["is_st"], errors="coerce").fillna(0)
        df["tradestatus"] = pd.to_numeric(df["tradestatus"], errors="coerce")
        return df
