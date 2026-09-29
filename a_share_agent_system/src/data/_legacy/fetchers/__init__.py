"""数据获取器模块

提供全A股、港股、新闻数据的异步获取能力。
"""

from src.data._legacy.fetchers.base_fetcher import BaseFetcher
from src.data._legacy.fetchers.a_share_fetcher import AShareFetcher
from src.data._legacy.fetchers.hk_stock_fetcher import HKStockFetcher
from src.data._legacy.fetchers.news_fetcher import NewsFetcher, fetch_batch_news
from src.data._legacy.fetchers.market_data_pipeline import MarketDataPipeline, run_pipeline

__all__ = [
    "BaseFetcher",
    "AShareFetcher",
    "HKStockFetcher",
    "NewsFetcher",
    "fetch_batch_news",
    "MarketDataPipeline",
    "run_pipeline",
]
