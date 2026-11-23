# -*- coding: utf-8 -*-
"""
脚本 04：全A股财务数据采集（业绩报表，Point-in-Time）
=====================================================
数据源：AkShare 东财业绩报表接口 `stock_yjbb_em(date=报告期)`
特性：按【报告期】一次返回全市场所有股票，10年共42期 ≈ 23万行

关键设计 —— Point-in-Time（无未来函数）：
  每条记录含「最新公告日期」（该期财报实际披露日），
  回测时用「公告日期 <= 交易日」的最新一期，杜绝使用未来数据。

产出: data/raw/financials_yjbb_partXXX.parquet（分片）
      data/processed/financials_yjbb.parquet（合并）
用法: python scripts/04_fetch_financials.py [--start 2016-03-31] [--end 2026-06-30]
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
from tqdm import tqdm  # noqa: E402

from src.storage import ensure_dirs, save_shard, merge_shards  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# 东财业绩报表的中文列名 → 规范英文列名（akshare 版本不同列名略有差异，按关键字匹配）
COLUMN_ALIASES = {
    "股票代码": "code",
    "股票简称": "name",
    "每股收益": "eps",
    "营业总收入-营业总收入": "revenue",
    "营业总收入-同比增长": "revenue_yoy",
    "营业总收入-季度环比增长": "revenue_qoq",
    "净利润-净利润": "net_profit",
    "净利润-同比增长": "net_profit_yoy",
    "净利润-季度环比增长": "net_profit_qoq",
    "每股净资产": "bps",
    "净资产收益率": "roe",
    "每股经营现金流量": "ocf_ps",
    "销售毛利率": "gross_margin",
    "所处行业": "industry",
    "最新公告日期": "pub_date",
}


def fetch_period(ak, report_date: str, retry: int = 3) -> pd.DataFrame:
    """拉取单个报告期的全市场业绩快照，失败自动重试"""
    for i in range(retry):
        try:
            df = ak.stock_yjbb_em(date=report_date)
            if df is None or df.empty:
                return pd.DataFrame()
            # 列名规范化（兼容不同 akshare 版本）
            rename = {k: v for k, v in COLUMN_ALIASES.items() if k in df.columns}
            df = df.rename(columns=rename)
            df["report_date"] = report_date  # 报告期
            return df
        except Exception as e:  # noqa: BLE001
            logger.warning("报告期 %s 拉取失败(第%d次): %s", report_date, i + 1, e)
            time.sleep(2 * (i + 1))
    return pd.DataFrame()


def quarter_end_dates(start: str, end: str) -> list[str]:
    """生成季度末日期序列: 2016-03-31 → 2026-06-30"""
    dates, y, q = [], int(start[:4]), int(start[5:7]) // 3
    while (y, q) <= (int(end[:4]), int(end[5:7]) // 3):
        m = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}[q]
        dates.append(f"{y}{m.replace('-', '')}")
        q += 1
        if q > 4:
            y, q = y + 1, 1
    return dates


def main() -> None:
    ap = argparse.ArgumentParser(description="全A股财务业绩采集")
    ap.add_argument("--start", default="2016-03-31")
    ap.add_argument("--end", default="2026-06-30")
    args = ap.parse_args()

    ensure_dirs()
    import akshare as ak

    periods = quarter_end_dates(args.start, args.end)
    logger.info("报告期数量: %d (%s ~ %s)", len(periods), periods[0], periods[-1])

    for i, p in enumerate(tqdm(periods, desc="采集财报", unit="期")):
        df = fetch_period(ak, p)
        if df.empty:
            logger.warning("报告期 %s 无数据，跳过", p)
            continue
        save_shard(df, "financials_yjbb", i)
        time.sleep(0.8)  # 限速，避免触发东财风控

    # 合并为最终数据集
    out = merge_shards("financials_yjbb", subdir="raw")
    merged = pd.read_parquet(out)
    logger.info("财务数据集: %d 条, 覆盖股票 %d 只, 期间 %s~%s",
                len(merged), merged["code"].nunique(),
                merged["report_date"].min(), merged["report_date"].max())


if __name__ == "__main__":
    main()
