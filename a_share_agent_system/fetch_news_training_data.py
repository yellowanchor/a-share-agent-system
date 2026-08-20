"""
训练用金融新闻数据获取 (v2)
--------------------------
批量获取全A股金融新闻，多页获取 + 去重 + 清洗，输出 JSONL。

数据源:
    - 东方财富个股新闻: 每只股票5页 × 100条 = 最多500条/只
    - 财新市场速递: 宏观财经快讯

输出格式 (JSONL):
    {"text": "标题+内容", "stock_code": "600519", "source": "eastmoney",
     "publish_time": "2026-06-05", "label": null}

预计数据量: 25,000-50,000 条
"""

import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent))
from src.utils.logger import setup_logging

logger = logging.getLogger(__name__)


# ====================================================================
#  修复版 stock_news_em（支持多页）
# ====================================================================

def _fetch_eastmoney_news(symbol: str, max_pages: int = 5) -> List[Dict]:
    """从东方财富获取个股新闻（多页拉取）。

    Args:
        symbol: 纯6位股票代码。
        max_pages: 最大拉取页数（每页最多100条）。

    Returns:
        新闻字典列表。
    """
    from curl_cffi import requests as _requests
    import json as _json

    all_articles = []

    for page in range(1, max_pages + 1):
        url = "https://search-api-web.eastmoney.com/search/jsonp"
        inner_param = {
            "uid": "",
            "keyword": symbol,
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientType": "web",
            "clientVersion": "curr",
            "param": {
                "cmsArticleWebOld": {
                    "searchScope": "default",
                    "sort": "default",
                    "pageIndex": page,
                    "pageSize": 100,
                    "preTag": "<em>",
                    "postTag": "</em>",
                }
            },
        }

        params = {
            "cb": "jQuery35101792940631092459_1764599530165",
            "param": _json.dumps(inner_param, ensure_ascii=False),
            "_": "1764599530176",
        }
        headers = {
            "accept": "*/*",
            "referer": f"https://so.eastmoney.com/news/s?keyword={symbol}",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

        try:
            r = _requests.get(url, params=params, headers=headers, timeout=15)
            data_text = r.text
            prefix = "jQuery35101792940631092459_1764599530165("
            if data_text.startswith(prefix):
                data_text = data_text[len(prefix):-1]

            data_json = _json.loads(data_text)
            articles = data_json.get("result", {}).get("cmsArticleWebOld", [])

            if not articles:
                break  # 没有更多数据，停止翻页

            for article in articles:
                title = str(article.get("title", "")).strip()
                content = str(article.get("content", "")).strip()

                # 清理 HTML 标签
                for tag in ["<em>", "</em>", "(<em>", "</em>)"]:
                    title = title.replace(tag, "")
                    content = content.replace(tag, "")
                content = content.replace("\u3000", " ").replace("\r\n", " ").replace("\n", " ")
                content = " ".join(content.split())  # 空白规范化
                title = " ".join(title.split())

                text = f"{title} {content}".strip()
                if len(text) < 20:
                    continue

                all_articles.append({
                    "text": text[:2000],
                    "stock_code": symbol,
                    "source": "eastmoney",
                    "publish_time": str(article.get("date", ""))[:10],
                    "title": title,
                    "label": None,
                })

            if len(articles) < 100:
                break  # 最后一页

        except Exception:
            break

    return all_articles


# ====================================================================
#  批量新闻获取流水线
# ====================================================================

async def fetch_all_news(
    codes: Optional[List[str]] = None,
    output_path: str = "./data/processed/financial_news.jsonl",
    max_pages_per_stock: int = 5,
) -> Dict:
    """批量获取全 A 股金融新闻数据。

    Args:
        codes: 股票代码列表（None 则自动获取沪深300）。
        output_path: JSONL 输出路径。
        max_pages_per_stock: 每只股票最大翻页数。

    Returns:
        {"total": int, "path": str, "size_mb": float}
    """
    from src.data.fetchers.market_data_pipeline import MarketDataPipeline
    from src.data.fetchers.news_fetcher import NewsFetcher
    import akshare as ak

    # 获取股票列表
    if codes is None:
        logger.info("获取沪深300成分股...")
        pipeline = MarketDataPipeline()
        constituents = pipeline.get_hs300_constituents()
        codes = constituents["code"].tolist()
    logger.info("共 %d 只股票", len(codes))

    output_dir = Path(output_path).parent
    output_dir.mkdir(parents=True, exist_ok=True)

    all_articles = []
    source_stats = {"eastmoney": 0, "market_summary": 0}

    # ================================================================
    #  数据源 1: 东方财富个股新闻 (多页)
    # ================================================================
    logger.info("数据源 1: 东方财富个股新闻 (%d 页/只)", max_pages_per_stock)

    for code in tqdm(codes, desc="个股新闻"):
        try:
            pure_code = code.split(".")[-1] if "." in code else code
            articles = _fetch_eastmoney_news(pure_code, max_pages=max_pages_per_stock)
            all_articles.extend(articles)
            source_stats["eastmoney"] += len(articles)
        except Exception as e:
            logger.debug("股票 %s 新闻获取失败: %s", code, str(e)[:80])

    logger.info("东方财富: %d 条", source_stats["eastmoney"])

    # ================================================================
    #  数据源 2: 财新市场速递
    # ================================================================
    logger.info("数据源 2: 财新市场速递...")
    try:
        df_main = ak.stock_news_main_cx()
        if not df_main.empty:
            for _, row in df_main.iterrows():
                text = str(row.get("summary", ""))
                if len(text) < 20:
                    continue
                all_articles.append({
                    "text": text[:2000],
                    "stock_code": "market",
                    "source": "market_summary",
                    "publish_time": datetime.now().strftime("%Y-%m-%d"),
                    "title": str(row.get("tag", "")),
                    "label": None,
                })
                source_stats["market_summary"] += 1
        logger.info("市场速递: %d 条", source_stats["market_summary"])
    except Exception as e:
        logger.warning("市场速递获取失败: %s", e)

    # ================================================================
    #  SimHash 去重
    # ================================================================
    before = len(all_articles)
    logger.info("SimHash 去重 (%d 条)...", before)

    if all_articles:
        fetcher = NewsFetcher(cache_dir=str(output_dir), dedup_threshold=3)
        all_articles = fetcher._simhash_dedup(all_articles)
        # 移除 dedup 中添加的临时字段
        for item in all_articles:
            item.pop("title", None)
            item.pop("content", None)

    after = len(all_articles)
    logger.info("去重: %d -> %d (剔除 %d 条)", before, after, before - after)

    # ================================================================
    #  输出 JSONL
    # ================================================================
    logger.info("写入 JSONL: %s", output_path)
    with open(output_path, "w", encoding="utf-8") as f:
        for item in all_articles:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    size_mb = Path(output_path).stat().st_size / (1024 * 1024)

    result = {
        "total": len(all_articles),
        "path": output_path,
        "size_mb": round(size_mb, 2),
        "per_source": source_stats,
    }

    logger.info("=" * 50)
    logger.info("新闻获取完成: %d 条 (%.1f MB)", result["total"], result["size_mb"])
    logger.info("数据源: %s", result["per_source"])
    return result


# ====================================================================
#  便捷入口
# ====================================================================

async def main():
    setup_logging(level="INFO", log_file="./logs/news_fetch.log")
    print("=" * 60)
    print("  金融新闻训练数据获取 (v2)")
    print(f"  启动: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    result = await fetch_all_news(
        codes=None,                 # 自动获取沪深300
        output_path="./data/processed/financial_news.jsonl",
        max_pages_per_stock=5,      # 每只股票最多5页
    )

    print(f"\n结果: {result['total']} 条新闻")
    print(f"文件: {result['path']} ({result['size_mb']:.1f} MB)")
    print(f"数据源: {result['per_source']}")

    # 按股票分布展示
    import json
    with open(result["path"], "r", encoding="utf-8") as f:
        samples = [json.loads(line) for line in list(f)[:5]]
    print("\n样例:")
    for s in samples:
        print(f"  [{s['stock_code']}] {s.get('title', s['text'][:50])[:80]}")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
