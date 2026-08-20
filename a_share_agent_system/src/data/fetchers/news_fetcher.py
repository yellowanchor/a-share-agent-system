"""
NewsFetcher — 新闻舆情数据获取器
--------------------------------
根据股票代码获取相关公告/新闻，支持：
    - 多数据源：AkShare（东方财富新闻）+ 巨潮资讯网公告
    - SimHash 去重
    - HTML标签清洗
    - DuckDB 结构化存储
"""

import asyncio
import hashlib
import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from src.data.fetchers.base_fetcher import BaseFetcher

logger = logging.getLogger(__name__)

# HTML 标签正则（预编译，提升清洗效率）
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
_WHITESPACE_PATTERN = re.compile(r"\s+")
_ENTITY_PATTERN = re.compile(r"&[a-z]+;")


class NewsFetcher(BaseFetcher):
    """新闻舆情数据获取器。

    获取指定股票的公告、新闻、研报等文本数据，
    经过去重、清洗后存入 DuckDB 供后续 RAG 检索使用。

    Attributes:
        dedup_threshold: SimHash 汉明距离阈值（默认3）。
        news_limit: 单次获取新闻数量上限。
    """

    # SimHash 去重阈值（汉明距离 <= 3 视为重复）
    DEDUP_BIT_COUNT: int = 64

    def __init__(
        self,
        dedup_threshold: int = 3,
        news_limit: int = 200,
        cache_dir: str = "./data/processed",
        max_retries: int = 3,
    ) -> None:
        """初始化 NewsFetcher。

        Args:
            dedup_threshold: SimHash 汉明距离阈值，小于等于此值视为重复。
            news_limit: 每只股票每次获取新闻数量上限。
            cache_dir: 缓存目录。
            max_retries: 最大重试次数。
        """
        super().__init__(cache_dir=cache_dir, max_retries=max_retries)
        self.dedup_threshold = dedup_threshold
        self.news_limit = news_limit

        # SimHash 指纹存储（去重用）
        self._fingerprints: List[int] = []

    # ------------------------------------------------------------------
    #  抽象方法实现
    # ------------------------------------------------------------------

    async def fetch_daily_kline(self, *args, **kwargs) -> pd.DataFrame:
        """NewsFetcher 不实现日K线接口。"""
        raise NotImplementedError("NewsFetcher 不支持日K线获取，请使用 fetch_news()")

    # ------------------------------------------------------------------
    #  新闻获取主接口
    # ------------------------------------------------------------------

    async def fetch_news(
        self,
        code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        sources: Optional[List[str]] = None,
    ) -> List[Dict]:
        """获取指定股票的新闻/公告。

        Args:
            code: 股票代码，如 "600519"。
            start_date: 起始日期 "YYYY-MM-DD"（None=近6个月）。
            end_date: 截止日期（None=今天）。
            sources: 数据源列表 ["eastmoney", "cninfo"]。

        Returns:
            新闻列表，每条格式: {"title", "content", "publish_time", "source", "stock_code"}
        """
        # 提取纯6位代码（去 sh./sz. 前缀）
        pure_code = self._extract_code(code)

        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")
        if start_date is None:
            start_date = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
        if sources is None:
            sources = ["eastmoney"]

        all_news = []

        for source in sources:
            try:
                news_items = await self._run_sync_in_executor(
                    self._fetch_from_source, pure_code, start_date, end_date, source
                )
                all_news.extend(news_items)
                logger.debug("数据源 %s: 获取 %d 条", source, len(news_items))
            except Exception as e:
                logger.warning("数据源 %s 获取失败: %s", source, e)

        # 去重
        all_news = self._simhash_dedup(all_news)

        # 清洗
        all_news = [self._clean_news(item) for item in all_news]

        logger.info("股票 %s 新闻获取完成: %d 条 (去重后)", pure_code, len(all_news))
        return all_news

    # ------------------------------------------------------------------
    #  各数据源实现
    # ------------------------------------------------------------------

    def _fetch_from_source(
        self, code: str, start_date: str, end_date: str, source: str
    ) -> List[Dict]:
        """从指定数据源获取新闻。

        Args:
            code: 纯6位股票代码。
            start_date: 起始日期。
            end_date: 截止日期。
            source: 数据源标识: "eastmoney" / "cninfo"。

        Returns:
            新闻字典列表。
        """
        if source == "eastmoney":
            return self._fetch_eastmoney_news(code, start_date, end_date)
        elif source == "cninfo":
            return self._fetch_cninfo_announcements(code, start_date, end_date)
        else:
            logger.warning("未知数据源: %s", source)
            return []

    def _fetch_eastmoney_news(
        self, code: str, start_date: str, end_date: str
    ) -> List[Dict]:
        """从东方财富获取个股新闻。

        AkShare 的 stock_news_em 接口提供免费新闻数据。

        Args:
            code: 纯6位股票代码。
            start_date: 起始日期。
            end_date: 截止日期。

        Returns:
            新闻列表。
        """
        import akshare as ak

        try:
            df = ak.stock_news_em(symbol=code)
            if df is None or df.empty:
                return []

            news_list = []
            for _, row in df.iterrows():
                if len(news_list) >= self.news_limit:
                    break

                pub_time = row.get("发布时间", "")
                if pub_time and start_date <= str(pub_time)[:10] <= end_date:
                    news_list.append({
                        "title": str(row.get("标题", "")).strip(),
                        "content": str(row.get("内容", "")).strip(),
                        "publish_time": str(pub_time),
                        "source": "eastmoney",
                        "stock_code": code,
                    })

            return news_list
        except Exception as e:
            logger.warning("东方财富新闻获取失败: %s", e)
            return []

    def _fetch_cninfo_announcements(
        self, code: str, start_date: str, end_date: str
    ) -> List[Dict]:
        """从巨潮资讯网获取公司公告（预留接口）。

        Args:
            code: 纯6位股票代码。
            start_date: 起始日期。
            end_date: 截止日期。

        Returns:
            公告列表。
        """
        # TODO: 巨潮资讯网 API 需申请 access token，当前返回空
        logger.debug("巨潮资讯网公告获取暂未实现 (code=%s)", code)
        return []

    # ------------------------------------------------------------------
    #  SimHash 去重
    # ------------------------------------------------------------------

    def _simhash_dedup(self, news_list: List[Dict]) -> List[Dict]:
        """基于 SimHash 的新闻去重。

        SimHash 是局部敏感哈希（LSH）的一种，相似文本产生相近的哈希值。
        通过比较汉明距离，可以有效识别内容高度重复的新闻（如同一天多家媒体的转载）。

        流程:
            1. 计算每条新闻的 SimHash 指纹（64位整数）
            2. 与已有指纹逐一计算汉明距离
            3. 距离 <= threshold 视为重复，剔除

        Args:
            news_list: 待去重新闻列表。

        Returns:
            去重后的新闻列表。
        """
        if not news_list:
            return news_list

        unique_news = []
        fingerprints = []

        for item in news_list:
            text = item.get("title", "") + item.get("content", "")
            if not text.strip():
                continue

            try:
                fp = self._simhash(text)
            except Exception:
                # SimHash 计算失败时用 content hash 替代
                fp = int(hashlib.md5(text.encode()).hexdigest()[:16], 16)

            # 检查是否与已有新闻重复
            is_dup = False
            for existing_fp in fingerprints:
                if self._hamming_distance(fp, existing_fp) <= self.dedup_threshold:
                    is_dup = True
                    break

            if not is_dup:
                unique_news.append(item)
                fingerprints.append(fp)

        dup_count = len(news_list) - len(unique_news)
        if dup_count > 0:
            logger.info("SimHash 去重: 剔除 %d 条重复新闻", dup_count)

        return unique_news

    def _simhash(self, text: str) -> int:
        """计算文本的 SimHash 指纹。

        SimHash 算法:
            1. 对文本分词（中文按单字/英文按空格）
            2. 每个词哈希为64位二进制向量
            3. 所有向量加权求和（出现次数多的词权重更高）
            4. 降维：正数位为1，负数位为0

        Args:
            text: 输入文本。

        Returns:
            64位 SimHash 指纹。
        """
        # 分词：中文按2-gram（双字），英文按空格
        cleaned = _ENTITY_PATTERN.sub(" ", text)
        tokens = self._tokenize(cleaned)

        if not tokens:
            return 0

        # 词频统计
        word_freq: Dict[str, int] = {}
        for token in tokens:
            word_freq[token] = word_freq.get(token, 0) + 1

        # 64位累加向量
        v = [0] * self.DEDUP_BIT_COUNT

        for word, freq in word_freq.items():
            # 词的64位哈希
            h = int(hashlib.md5(word.encode()).hexdigest()[:16], 16)
            for i in range(self.DEDUP_BIT_COUNT):
                if (h >> i) & 1:
                    v[i] += freq
                else:
                    v[i] -= freq

        # 降维：>0 为 1，<=0 为 0
        fingerprint = 0
        for i in range(self.DEDUP_BIT_COUNT):
            if v[i] > 0:
                fingerprint |= (1 << i)

        return fingerprint

    def _tokenize(self, text: str) -> List[str]:
        """对文本进行简单分词。

        中文: 2-gram（双字滑动窗口）
        英文/数字: 按空格和标点分割
        混合: 两种策略结合

        Args:
            text: 输入文本。

        Returns:
            词条列表。
        """
        tokens = []

        # 中文双字滑动窗口
        chinese_chars = re.findall(r"[\u4e00-\u9fff]", text)
        for i in range(len(chinese_chars) - 1):
            tokens.append(chinese_chars[i] + chinese_chars[i + 1])

        # 英文/数字按空格分割
        alpha_tokens = re.findall(r"[a-zA-Z0-9]+", text)
        tokens.extend(t.lower() for t in alpha_tokens if len(t) >= 2)

        return tokens

    @staticmethod
    def _hamming_distance(a: int, b: int) -> int:
        """计算两个整数的汉明距离（二进制位不同的个数）。

        Python 3.8+ 可用 int.bit_count() 高效实现。

        Args:
            a: 整数a。
            b: 整数b。

        Returns:
            汉明距离（0-64）。
        """
        return (a ^ b).bit_count()

    # ------------------------------------------------------------------
    #  文本清洗
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_news(item: Dict) -> Dict:
        """清洗单条新闻：去除HTML标签、空白规范化、截断过长内容。

        清洗规则:
            1. 移除所有 HTML 标签 (<br>, <div>, <p> 等)
            2. 合并连续空白字符为单个空格
            3. 去除首尾空白
            4. 内容过长时截断（保留前2000字符用于向量化）

        Args:
            item: 原始新闻字典。

        Returns:
            清洗后的新闻字典。
        """
        for field in ("title", "content"):
            text = item.get(field, "")
            if text:
                # 去除HTML标签
                text = _HTML_TAG_PATTERN.sub("", text)
                # HTML 实体解码
                text = text.replace("&nbsp;", " ").replace("&amp;", "&")
                text = text.replace("&lt;", "<").replace("&gt;", ">")
                text = text.replace("&quot;", '"')
                # 空白规范化
                text = _WHITESPACE_PATTERN.sub(" ", text).strip()
                item[field] = text

        # 内容截断（前2000字符，保留上下文完整性）
        content = item.get("content", "")
        if len(content) > 2000:
            item["content"] = content[:2000] + "..."

        return item

    # ------------------------------------------------------------------
    #  DuckDB 存储
    # ------------------------------------------------------------------

    @staticmethod
    def to_duckdb(
        news_list: List[Dict],
        db_path: str = "./data/processed/market_data.duckdb",
    ) -> int:
        """将清洗后的新闻数据写入 DuckDB。

        表结构:
            news_articles (
                id INTEGER PRIMARY KEY,
                title TEXT,
                content TEXT,
                publish_time TIMESTAMP,
                source VARCHAR,
                stock_code VARCHAR,
                content_hash VARCHAR
            )

        Args:
            news_list: 新闻字典列表。
            db_path: DuckDB 数据库路径。

        Returns:
            写入的记录数。
        """
        if not news_list:
            return 0

        import duckdb

        # 转换为 DataFrame
        df = pd.DataFrame(news_list)
        df["content_hash"] = df.apply(
            lambda r: hashlib.md5(
                (r.get("title", "") + r.get("content", "")).encode()
            ).hexdigest(),
            axis=1,
        )

        # 写入 DuckDB（去重写入：相同 content_hash 不重复插入）
        con = duckdb.connect(db_path)
        con.execute(
            """CREATE TABLE IF NOT EXISTS news_articles (
                id INTEGER PRIMARY KEY DEFAULT unique_rowid(),
                title TEXT,
                content TEXT,
                publish_time TIMESTAMP,
                source VARCHAR,
                stock_code VARCHAR,
                content_hash VARCHAR UNIQUE
            )"""
        )
        con.execute(
            "INSERT OR IGNORE INTO news_articles "
            "SELECT NULL, title, content, publish_time, source, stock_code, content_hash "
            "FROM df"
        )
        count = con.execute("SELECT COUNT(*) FROM news_articles").fetchone()[0]
        con.close()

        logger.info("DuckDB 新闻写入完成: 总 %d 条", count)
        return count

    # ------------------------------------------------------------------
    #  工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_code(code: str) -> str:
        """从各种输入格式中提取纯6位数字代码。"""
        code = code.strip()
        if "." in code:
            code = code.split(".")[-1]
        return code.zfill(6)


# ------------------------------------------------------------------
#  便捷函数
# ------------------------------------------------------------------

async def fetch_batch_news(
    codes: List[str],
    output_dir: str = "./data/processed",
) -> List[Dict]:
    """批量获取多只股票的新闻并写入 DuckDB。

    Args:
        codes: 股票代码列表。
        output_dir: 数据输出目录。

    Returns:
        去重清洗后的新闻列表。
    """
    fetcher = NewsFetcher(cache_dir=output_dir)
    all_news = []

    for code in codes:
        try:
            news = await fetcher.fetch_news(code)
            all_news.extend(news)
        except Exception as e:
            logger.warning("股票 %s 新闻获取失败: %s", code, e)

    # 全量写入 DuckDB
    db_path = str(Path(output_dir) / "market_data.duckdb")
    NewsFetcher.to_duckdb(all_news, db_path)

    logger.info("批量新闻获取完成: %d 条", len(all_news))
    return all_news
