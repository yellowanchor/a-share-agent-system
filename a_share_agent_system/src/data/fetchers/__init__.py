"""数据获取器模块

提供全A股、港股、新闻数据的异步获取能力。
"""

from src.data.fetchers.base_fetcher import BaseFetcher
from src.data.fetchers.a_share_fetcher import AShareFetcher
from src.data.fetchers.hk_stock_fetcher import HKStockFetcher
from src.data.fetchers.news_fetcher import NewsFetcher, fetch_batch_news
from src.data.fetchers.market_data_pipeline import MarketDataPipeline, run_pipeline

__all__ = [
    "BaseFetcher",
    "AShareFetcher",
    "HKStockFetcher",
    "NewsFetcher",
    "fetch_batch_news",
    "MarketDataPipeline",
    "run_pipeline",
]
