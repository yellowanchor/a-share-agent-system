# -*- coding: utf-8 -*-
"""
build_rag_index.py — 构建新闻向量索引（bge-m3 + LocalVectorStore）
==================================================================
将 data/processed/news.parquet 全量向量化并落盘为 numpy 索引：
    data/vector_store/embeddings.npy  (fp32, N×1024, L2 归一化)
    data/vector_store/meta.parquet    (id/title/text/... 元数据)
    data/vector_store/index_meta.json (索引元信息)

说明: 原方案 ChromaDB 1.5.9 (Rust) 在 Windows 上 HNSW 无法持久化，
已改为自研 LocalVectorStore（numpy 精确余弦检索），接口不变。

用法:
    python scripts/build_rag_index.py [--limit N]
"""

import argparse
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("build_rag_index")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

NEWS_PATH = ROOT.parent / "data_platform" / "data" / "processed" / "news.parquet"
INDEX_DIR = ROOT / "data" / "vector_store"
MODEL_PATH = ROOT / "models" / "bge-m3"


def main() -> None:
    parser = argparse.ArgumentParser(description="构建 RAG 新闻向量索引")
    parser.add_argument("--limit", type=int, default=None, help="仅处理前 N 条（调试）")
    parser.add_argument("--batch", type=int, default=64, help="编码批次大小")
    args = parser.parse_args()

    import pandas as pd

    logger.info("读取新闻数据: %s", NEWS_PATH)
    df = pd.read_parquet(NEWS_PATH)
    if args.limit:
        df = df.head(args.limit)
    logger.info("待向量化: %d 条", len(df))

    # 加载 bge-m3（GPU 优先）
    import torch
    from sentence_transformers import SentenceTransformer
    device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info("加载 bge-m3 (device=%s): %s", device, MODEL_PATH)
    model = SentenceTransformer(str(MODEL_PATH), device=device)

    # 构建索引（向量化 + 落盘）
    from src.models.rag.local_store import LocalVectorStore
    t0 = time.time()
    store = LocalVectorStore.build(df, model, INDEX_DIR, batch_size=args.batch)
    logger.info("索引构建完成: %d 条, 总耗时 %.1fs", store.count, time.time() - t0)

    # 落盘后用新进程可读性自检（同进程内直接检索验证）
    q = model.encode(["贵州茅台 业绩"], normalize_embeddings=True,
                     convert_to_numpy=True)
    hits = store.search(q, top_k=3)
    logger.info("自检检索 top1: [%.4f] %s", hits[0]["score"], hits[0]["title"][:50])
    logger.info("全部完成 ✅")


if __name__ == "__main__":
    main()
