# -*- coding: utf-8 -*-
"""
retriever.py — 新闻语义检索器（RAG 查询端）
==========================================
基于 LocalVectorStore（numpy 精确余弦）+ BAAI/bge-m3 查询编码：
    - 支持按股票代码过滤
    - 支持时间范围过滤
    - 返回 top-k 相关新闻及其相似度分数

说明: 原方案 ChromaDB 1.5.9 (Rust) 在 Windows 上 HNSW 索引无法持久化，
已替换为自研 LocalVectorStore（见 src/models/rag/local_store.py）。

用法:
    from src.models.rag.retriever import NewsRetriever
    retriever = NewsRetriever()
    results = retriever.search("贵州茅台 业绩 增长", stock_code="600519", top_k=5)
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# 项目根目录（src/models/rag/retriever.py → 向上 3 级）
ROOT = Path(__file__).resolve().parents[3]

DEFAULT_INDEX_DIR = ROOT / "data" / "vector_store"
DEFAULT_MODEL_PATH = ROOT / "models" / "bge-m3"


class NewsRetriever:
    """新闻语义检索器。

    Attributes:
        index_dir: 向量索引目录（embeddings.npy + meta.parquet）。
        model: bge-m3 查询编码模型（懒加载）。
    """

    def __init__(
        self,
        index_dir: Optional[Path] = None,
        model_path: Optional[Path] = None,
        load_model: bool = True,
    ) -> None:
        """初始化检索器。

        Args:
            index_dir: 向量索引目录。
            model_path: bge-m3 本地模型路径。
            load_model: 是否立即加载编码模型（False 时首次检索再加载）。

        Raises:
            FileNotFoundError: 索引目录不存在。
        """
        from src.models.rag.local_store import LocalVectorStore

        self.index_dir = Path(index_dir) if index_dir else DEFAULT_INDEX_DIR
        self._model_path = Path(model_path) if model_path else DEFAULT_MODEL_PATH
        self._store = LocalVectorStore(self.index_dir)
        self._model = None
        if load_model:
            self._ensure_model()
        logger.info(
            "检索器就绪: 索引=%s, 文档数=%d",
            self.index_dir, self._store.count,
        )

    # ------------------------------------------------------------------
    #  模型
    # ------------------------------------------------------------------

    @property
    def count(self) -> int:
        """库内文档总数。"""
        return self._store.count

    def _ensure_model(self) -> None:
        """懒加载 bge-m3 编码模型（GPU 优先）。"""
        if self._model is not None:
            return
        from sentence_transformers import SentenceTransformer
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            device = "cpu"
        self._model = SentenceTransformer(str(self._model_path), device=device)
        logger.info("bge-m3 编码模型就绪 (device=%s)", device)

    def encode_query(self, query: str) -> np.ndarray:
        """将查询文本编码为归一化向量。"""
        self._ensure_model()
        return np.asarray(
            self._model.encode([query], normalize_embeddings=True,
                               convert_to_numpy=True)[0],
            dtype=np.float32,
        )

    # ------------------------------------------------------------------
    #  语义检索
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        stock_code: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        top_k: int = 5,
        query_embedding: Optional[np.ndarray] = None,
    ) -> List[Dict]:
        """执行语义检索。

        Args:
            query: 查询文本（如 "贵州茅台 业绩增长"）。
            stock_code: 过滤股票代码（6 位数字），None 表示全市场。
            start: 过滤发布时间下限 "YYYY-MM-DD HH:MM:SS"。
            end: 过滤发布时间上限。
            top_k: 返回条数。
            query_embedding: 预计算查询向量（避免重复编码）。

        Returns:
            [{"title", "text", "source", "stock_code", "publish_time",
              "score", "id"}, ...] 按相似度降序。
        """
        if query_embedding is None:
            query_embedding = self.encode_query(query)
        return self._store.search(
            query_embedding, top_k=top_k,
            stock_code=stock_code, start=start, end=end,
        )

    # ------------------------------------------------------------------
    #  按股票取最近新闻（结构化兜底）
    # ------------------------------------------------------------------

    def recent_by_stock(self, stock_code: str, limit: int = 10) -> List[Dict]:
        """按股票代码取最近新闻（按 publish_time 降序，结构化查询）。

        Args:
            stock_code: 6 位股票代码。
            limit: 条数。

        Returns:
            新闻列表（含 score=None，表示非相似度排序）。
        """
        return self._store.recent_by_stock(stock_code, limit=limit)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    r = NewsRetriever()
    for item in r.search("茅台 业绩 增长 2026 半年报", stock_code="600519", top_k=3):
        print(f"[{item['score']}] {item['title']} | {item['publish_time']}")
