# -*- coding: utf-8 -*-
"""增量采集层：行情/新闻抓取与入库（日更流水线）。"""

from .market_fetch import (
    fetch_em_bars,
    fetch_raw_bars,
    fetch_tencent_bars,
    secid,
    tencent_code,
    to_db_rows,
)
from .news_fetch import fetch_news, fetch_many, news_id, normalize_url

__all__ = [
    "fetch_raw_bars", "fetch_em_bars", "fetch_tencent_bars",
    "secid", "tencent_code", "to_db_rows",
    "fetch_news", "fetch_many", "news_id", "normalize_url",
]
