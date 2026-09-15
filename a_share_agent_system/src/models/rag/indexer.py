# -*- coding: utf-8 -*-
"""
VectorIndexer — RAG 向量索引构建器
-----------------------------------
使用 bge-m3 嵌入模型将新闻文本向量化，写入自研 LocalVectorStore
（numpy 精确余弦检索），支持全量构建与幂等增量更新。

历史: 原实现基于 ChromaDB 1.5.9，因其 Rust 内核在 Windows 上存在
HNSW 索引无法持久化的缺陷（图文件永不落盘）已弃用，
详见 src/models/rag/local_store.py 文件头说明。
"""

import hashlib
import logging
from pathlib import Path
from typing import Dict, List, Optional

import duckdb
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 项目根目录（src/models/rag/indexer.py → 向上 3 级）
ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MODEL_PATH = ROOT / "models" / "bge-m3"


def _stable_id(item: Dict) -> str:
    """基于 url 生成稳定文档 ID（幂等，支持增量去重）。

    Args:
        item: 新闻字典（需含 url 或 title+stock_code）。

    Returns:
        形如 news_<16位hex> 的稳定 ID。
    """
    url = str(item.get("url", "")).strip()
    if url:
        return f"news_{hashlib.md5(url.encode('utf-8')).hexdigest()[:16]}"
    # 无 url 时退化为 title+code+time 的 hash
    raw = f"{item.get('title','')}|{item.get('stock_code','')}|{item.get('publish_time','')}"
    return f"news_{hashlib.md5(raw.encode('utf-8')).hexdigest()[:16]}"


class VectorIndexer:
    """RAG 向量索引构建器（LocalVectorStore 版）。

    Attributes:
        model_path: bge-m3 本地模型目录。
        vector_store_dir: 向量库存储目录（embeddings.npy + meta.parquet）。
        batch_size: 向量化批次大小（受限于 GPU 显存）。
    """

    def __init__(
        self,
        model_path: Optional[Path] = None,
        vector_store_dir: str = "./data/vector_store",
        batch_size: int = 64,
    ) -> None:
        """初始化向量索引构建器。

        Args:
            model_path: bge-m3 本地模型路径（默认 models/bge-m3）。
            vector_store_dir: LocalVectorStore 存储目录。
            batch_size: 模型推理批次大小。
        """
        self.model_path = Path(model_path) if model_path else DEFAULT_MODEL_PATH
        self.vector_store_dir = Path(vector_store_dir)
        self.vector_store_dir.mkdir(parents=True, exist_ok=True)
        self.batch_size = batch_size
        self._model = None

    # ------------------------------------------------------------------
    #  嵌入模型加载
    # ------------------------------------------------------------------

    def load_embedding_model(self) -> object:
        """加载 bge-m3 嵌入模型（GPU 优先，回退 CPU）。"""
        if self._model is not None:
            return self._model
        from sentence_transformers import SentenceTransformer
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            device = "cpu"
        self._model = SentenceTransformer(str(self.model_path), device=device)
        self._model.max_seq_length = 8192
        logger.info("嵌入模型加载完成: %s (设备: %s)", self.model_path, device)
        return self._model

    # ------------------------------------------------------------------
    #  向量化
    # ------------------------------------------------------------------

    def encode_texts(
        self,
        texts: List[str],
        show_progress: bool = True,
    ) -> np.ndarray:
        """将文本列表编码为 L2 归一化稠密向量（(N, 1024)）。"""
        model = self.load_embedding_model()
        non_empty_texts = [t if t.strip() else " " for t in texts]
        return np.asarray(model.encode(
            non_empty_texts,
            batch_size=self.batch_size,
            show_progress_bar=show_progress,
            normalize_embeddings=True,
            convert_to_numpy=True,
        ), dtype=np.float32)

    # ------------------------------------------------------------------
    #  索引构建 / 增量更新（LocalVectorStore）
    # ------------------------------------------------------------------

    @staticmethod
    def _to_dataframe(news_list: List[Dict]) -> pd.DataFrame:
        """新闻字典列表 → LocalVectorStore 要求的 DataFrame 列结构。"""
        rows = []
        for item in news_list:
            body = item.get("text") or item.get("content") or ""
            rows.append({
                "id": _stable_id(item),
                "title": str(item.get("title", "")),
                "text": str(body),
                "source": str(item.get("source", "")),
                "stock_code": str(item.get("stock_code", "")),
                "publish_time": str(item.get("publish_time", "")),
                "url": str(item.get("url", "")),
            })
        return pd.DataFrame(rows)

    def build_index(self, news_list: List[Dict]) -> int:
        """全量重建向量索引（覆盖既有索引）。

        Args:
            news_list: 新闻字典列表（需含 title, text/content, url 等字段）。

        Returns:
            入库文档数。
        """
        from src.models.rag.local_store import LocalVectorStore

        df = self._to_dataframe(news_list)
        if df.empty:
            logger.warning("新闻列表为空，跳过索引构建")
            return 0
        df = df.drop_duplicates(subset=["id"]).reset_index(drop=True)
        LocalVectorStore.build(
            df, self.load_embedding_model(),
            self.vector_store_dir, batch_size=self.batch_size,
        )
        return len(df)

    def incremental_update(self, news_list: List[Dict]) -> int:
        """增量更新向量索引（按 id 幂等去重, 原子落盘, 不重建）。

        Args:
            news_list: 新增新闻列表。

        Returns:
            实际新增文档数。
        """
        from src.models.rag.local_store import LocalVectorStore

        df = self._to_dataframe(news_list)
        if df.empty:
            return 0
        df = df.drop_duplicates(subset=["id"]).reset_index(drop=True)
        return LocalVectorStore.add_documents(
            df, self.load_embedding_model(),
            self.vector_store_dir, batch_size=self.batch_size,
        )

    # ------------------------------------------------------------------
    #  从 DuckDB 直接构建索引
    # ------------------------------------------------------------------

    def build_from_duckdb(
        self,
        db_path: str = "./data/processed/market_data.duckdb",
        limit: Optional[int] = None,
    ) -> int:
        """从 DuckDB 的 news 表读取新闻并全量构建索引。

        Args:
            db_path: DuckDB 数据库路径。
            limit: 限制读取条数（调试用），None 表示全部。

        Returns:
            入库文档数。
        """
        con = duckdb.connect(db_path, read_only=True)
        try:
            tables = {t[0] for t in con.execute("SHOW TABLES").fetchall()}
            if "news" not in tables:
                logger.warning("DuckDB 中无 news 表，请先完成数据采集")
                return 0
            query = "SELECT title, text, source, stock_code, publish_time, url FROM news"
            if limit:
                query += f" LIMIT {limit}"
            df = con.execute(query).fetchdf()
        finally:
            con.close()

        if df.empty:
            logger.warning("DuckDB 中无新闻数据")
            return 0
        logger.info("从 DuckDB 读取 %d 条新闻", len(df))
        return self.build_index(df.to_dict("records"))


# ------------------------------------------------------------------
#  便捷函数
# ------------------------------------------------------------------

def build_rag_index(
    db_path: str = "./data/processed/market_data.duckdb",
    vector_store_dir: str = "./data/vector_store",
    limit: Optional[int] = None,
) -> int:
    """一键构建 RAG 索引（从 DuckDB 读取 + 向量化 + 写入 LocalVectorStore）。"""
    indexer = VectorIndexer(vector_store_dir=vector_store_dir)
    return indexer.build_from_duckdb(db_path, limit=limit)


def incremental_rag_update(
    news_list: List[Dict],
    vector_store_dir: str = "./data/vector_store",
) -> int:
    """一键增量更新 RAG 索引（供每日新闻采集流水线调用）。"""
    indexer = VectorIndexer(vector_store_dir=vector_store_dir)
    return indexer.incremental_update(news_list)
