# -*- coding: utf-8 -*-
"""
脚本 01：获取全A股股票列表（含退市股）
=====================================
策略：取两个日期的股票列表取并集
  - 历史日期（2016-01-04）：当时已上市的公司 → 覆盖此后退市的股票
  - 最近交易日：当前上市的公司 → 覆盖此后新上市的公司
并集 = 2016~至今 曾上市的全部 A 股，规避机器学习中的"幸存者偏差"。

产出: data/raw/stock_list.parquet
      data/raw/trade_calendar.parquet（交易日历，供本脚本找最近交易日）
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# 允许直接以脚本运行（python scripts/01_fetch_stock_list.py）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from src.baostock_client import BaoStockClient  # noqa: E402
from src.storage import ensure_dirs, RAW_DIR  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

HIST_DATE = "2016-01-04"   # 起点：覆盖10年区间内所有曾上市股票
END_DATE = "2026-12-31"


def find_latest_trade_day(client: BaoStockClient) -> str:
    """从交易日历中找出 <= 今天 的最近一个交易日"""
    cal = client.get_trade_dates("2020-01-01", END_DATE)
    if cal.empty:
        raise RuntimeError("交易日历为空")
    cal = cal[cal["is_trading_day"] == "1"].copy()
    cal["calendar_date"] = pd.to_datetime(cal["calendar_date"])
    today = pd.Timestamp.today().normalize()
    latest = cal[cal["calendar_date"] <= today]["calendar_date"].max()
    if pd.isna(latest):
        raise RuntimeError("找不到最近交易日")
    return latest.strftime("%Y-%m-%d")


def main() -> None:
    ensure_dirs()
    with BaoStockClient() as client:
        # 1) 交易日历（同时保存，脚本02不再重复拉取）
        cal = client.get_trade_dates("2016-01-01", END_DATE)
        cal.to_parquet(RAW_DIR / "trade_calendar.parquet", index=False)
        logger.info("交易日历已保存: %d 行", len(cal))

        # 2) 历史日期列表（2016年已上市 → 含此后退市者）
        hist = client.get_all_stock(HIST_DATE)
        logger.info("历史日期 %s 上市股票: %d 只", HIST_DATE, len(hist))

        # 3) 最近交易日列表（当前上市）
        latest = find_latest_trade_day(client)
        now = client.get_all_stock(latest)
        logger.info("最近交易日 %s 上市股票: %d 只", latest, len(now))

    # 4) 合并去重：只保留沪深A股（BaoStock 不含北交所，北交所后续用 akshare 补充）
    df = pd.concat([hist, now], ignore_index=True).drop_duplicates(subset=["code"])
    # BaoStock 代码形如 sh.600000 / sz.000001
    df = df[df["code"].str.match(r"^(sh\.(60|68)|sz\.(00|30))", na=False)].copy()
    df["market"] = df["code"].str.split(".").str[0]          # sh / sz / bj
    df["symbol"] = df["code"].str.split(".").str[1]          # 6位数字代码
    df["board"] = df["symbol"].str[:2].map(
        {"60": "沪主板", "68": "科创板", "00": "深主板", "30": "创业板"}
    ).fillna("北交所")
    df = df.sort_values("code").reset_index(drop=True)

    # 5) 标记股票来源（历史期曾上市 / 当前期仍在市）
    hist_codes = set(hist["code"])
    now_codes = set(now["code"])
    df["in_hist"] = df["code"].isin(hist_codes)
    df["in_now"] = df["code"].isin(now_codes)
    delisted = int((df["in_hist"] & ~df["in_now"]).sum())   # 期间退市
    new_listed = int((df["in_now"] & ~df["in_hist"]).sum())  # 期间新上市

    out = RAW_DIR / "stock_list.parquet"
    df.to_parquet(out, index=False)
    logger.info("股票列表已保存: %s", out)
    logger.info(
        "共 %d 只A股 | 历史期 %d 只 | 当前期 %d 只 | 期间退市 %d 只 | 新上市 %d 只",
        len(df), df["in_hist"].sum(), df["in_now"].sum(), delisted, new_listed,
    )
    logger.info("板块分布:\n%s", df["board"].value_counts().to_string())


if __name__ == "__main__":
    main()
