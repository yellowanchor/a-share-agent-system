"""RAG 检索增强生成模块

Phase 1 实现:
    - bge-m3 向量索引构建
    - ChromaDB 存储与增量更新

Phase 2 实现:
    - 检索器 + 重排序
    - Recall@5 / MRR 评估
"""

from src.models.rag.indexer import VectorIndexer, build_rag_index

__all__ = ["VectorIndexer", "build_rag_index"]
