# -*- coding: utf-8 -*-
"""
local_store.py — 轻量本地向量库（numpy 精确余弦检索）
======================================================
背景: chromadb 1.5.9 (Rust 内核) 在 Windows 上存在 HNSW 索引无法持久化的
缺陷（图文件永不落盘，新进程加载必失败），故改用自研方案:
    - 向量: embeddings.npy (fp32, N×1024, 已 L2 归一化)
    - 元数据: meta.parquet (id/title/text/source/stock_code/publish_time/url)
    - 检索: numpy 暴力精确余弦（12.6 万×1024 维单次查询 <100ms）

优点: 零外部依赖、确定性落盘、结果精确（非近似搜索）。
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

META_FILE = "meta.parquet"
EMB_FILE = "embeddings.npy"
INDEX_META_FILE = "index_meta.json"

META_COLS = ["id", "title", "text", "source", "stock_code", "publish_time", "url"]


class LocalVectorStore:
    """基于 numpy 的本地向量库（精确余弦检索）。

    Attributes:
        index_dir: 索引目录（含 embeddings.npy / meta.parquet）。
        embeddings: (N, D) fp32 已归一化向量矩阵。
        meta: 新闻元数据 DataFrame。
    """

    def __init__(self, index_dir: Path, use_gpu: bool = True) -> None:
        self.index_dir = Path(index_dir)
        emb_path = self.index_dir / EMB_FILE
        meta_path = self.index_dir / META_FILE
        if not emb_path.exists() or not meta_path.exists():
            raise FileNotFoundError(
                f"向量索引不存在: {self.index_dir}，请先运行 scripts/build_rag_index.py"
            )
        # 全量载入内存（516MB fp32，换取每次查询免 mmap 拷贝）
        self.embeddings: np.ndarray = np.load(emb_path)
        self.meta: pd.DataFrame = pd.read_parquet(meta_path)
        meta_json = self.index_dir / INDEX_META_FILE
        self.index_meta: Dict = (
            json.loads(meta_json.read_text(encoding="utf-8"))
            if meta_json.exists() else {}
        )
        if len(self.meta) != self.embeddings.shape[0]:
            raise ValueError(
                f"索引不一致: 向量 {self.embeddings.shape[0]} 条 vs 元数据 {len(self.meta)} 条"
            )
        # GPU 加速（可用则将向量常驻显存，检索矩阵乘 <5ms）
        self._emb_gpu = None
        if use_gpu:
            try:
                import torch
                if torch.cuda.is_available():
                    self._emb_gpu = torch.from_numpy(self.embeddings).cuda()
                    logger.info("向量已载入 GPU 显存 (%.1f GB)",
                                self._emb_gpu.element_size() * self._emb_gpu.nelement() / 1e9)
            except Exception as e:  # pragma: no cover
                logger.warning("GPU 加速不可用，回退 numpy: %s", e)
        logger.info(
            "LocalVectorStore 就绪: %d 条 × %d 维 (%s)",
            self.embeddings.shape[0], self.embeddings.shape[1], self.index_dir,
        )

    # ------------------------------------------------------------------
    #  属性
    # ------------------------------------------------------------------

    @property
    def count(self) -> int:
        """库内文档总数。"""
        return int(self.embeddings.shape[0])

    # ------------------------------------------------------------------
    #  检索
    # ------------------------------------------------------------------

    def search(
        self,
        query_embedding: np.ndarray,
        top_k: int = 5,
        stock_code: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> List[Dict]:
        """精确余弦相似度检索 top-k。

        Args:
            query_embedding: (D,) 查询向量（会自动归一化）。
            top_k: 返回条数。
            stock_code: 过滤 6 位股票代码，None 表示全市场。
            start: 发布时间下限 "YYYY-MM-DD HH:MM:SS"。
            end: 发布时间上限。

        Returns:
            [{"title", "text", "source", "stock_code", "publish_time",
              "score", "id"}, ...] 按相似度降序。
        """
        q = np.asarray(query_embedding, dtype=np.float32).ravel()
        norm = np.linalg.norm(q)
        if norm > 0:
            q = q / norm

        # 过滤掩码（无过滤时走快速路径，避免整块 fancy-index 拷贝）
        has_filter = bool(stock_code or start or end)
        if has_filter:
            mask = np.ones(len(self.meta), dtype=bool)
            if stock_code:
                six = str(stock_code).strip().lower().split(".")[-1]
                mask &= (self.meta["stock_code"].astype(str) == six).to_numpy()
            if start:
                mask &= (self.meta["publish_time"].astype(str) >= start).to_numpy()
            if end:
                mask &= (self.meta["publish_time"].astype(str) <= end).to_numpy()
            cand = np.nonzero(mask)[0]
            if cand.size == 0:
                return []
            scores = self._matvec(self.embeddings[cand], q)
        else:
            cand = np.arange(len(self.meta))
            scores = self._matvec(self.embeddings, q)
        k = min(top_k, cand.size)
        if k < cand.size:
            part = np.argpartition(-scores, k - 1)[:k]
        else:
            part = np.arange(cand.size)
        order = part[np.argsort(-scores[part])]
        best = cand[order]

        items: List[Dict] = []
        sub = self.meta.iloc[best]
        for i, (_, row) in enumerate(sub.iterrows()):
            items.append({
                "title": str(row.get("title", "")),
                "text": str(row.get("text", "")),
                "source": str(row.get("source", "")),
                "stock_code": str(row.get("stock_code", "")),
                "publish_time": str(row.get("publish_time", "")),
                "score": round(float(scores[order[i]]), 4),
                "id": str(row.get("id", "")),
            })
        return items

    def recent_by_stock(self, stock_code: str, limit: int = 10) -> List[Dict]:
        """按股票代码取最近新闻（publish_time 降序）。"""
        six = str(stock_code).strip().lower().split(".")[-1]
        sub = self.meta[self.meta["stock_code"].astype(str) == six]
        sub = sub.sort_values("publish_time", ascending=False).head(limit)
        items: List[Dict] = []
        for _, row in sub.iterrows():
            items.append({
                "title": str(row.get("title", "")),
                "text": str(row.get("text", "")),
                "source": str(row.get("source", "")),
                "stock_code": str(row.get("stock_code", "")),
                "publish_time": str(row.get("publish_time", "")),
                "score": None,
                "id": str(row.get("id", "")),
            })
        return items

    # ------------------------------------------------------------------
    #  内部工具
    # ------------------------------------------------------------------

    def _matvec(self, mat: np.ndarray, q: np.ndarray) -> np.ndarray:
        """矩阵×向量（GPU 优先，回退 numpy）。"""
        if self._emb_gpu is not None:
            import torch
            with torch.no_grad():
                if mat is self.embeddings:  # 全量快速路径
                    s = self._emb_gpu @ torch.from_numpy(q).cuda()
                else:
                    s = torch.from_numpy(np.ascontiguousarray(mat)).cuda() @ torch.from_numpy(q).cuda()
                return s.float().cpu().numpy()
        return np.asarray(mat) @ q

    # ------------------------------------------------------------------
    #  构建
    # ------------------------------------------------------------------

    @classmethod
    def build(
        cls,
        news_df: pd.DataFrame,
        model,
        index_dir: Path,
        batch_size: int = 64,
        log_every: int = 20,
    ) -> "LocalVectorStore":
        """将新闻 DataFrame 向量化并落盘。

        Args:
            news_df: 含 title/text/source/stock_code/publish_time/url 列。
            model: SentenceTransformer 实例（normalize_embeddings=True）。
            index_dir: 输出目录。
            batch_size: 编码批次大小。
            log_every: 每 N 批打印一次进度。

        Returns:
            构建完成的 LocalVectorStore 实例。
        """
        index_dir = Path(index_dir)
        index_dir.mkdir(parents=True, exist_ok=True)

        df = news_df.reset_index(drop=True).copy()
        # 稳定 id：md5(url)，与去重逻辑一致
        import hashlib
        if "id" not in df.columns or df["id"].isna().any():
            df["id"] = [
                "news_" + hashlib.md5(str(u).encode("utf-8")).hexdigest()[:16]
                for u in df.get("url", pd.Series([""] * len(df)))
            ]
        texts = (df["title"].astype(str) + " " + df["text"].astype(str)).tolist()

        import time
        t0 = time.time()
        emb = model.encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=True,
            show_progress_bar=True,
            convert_to_numpy=True,
        )
        emb = np.asarray(emb, dtype=np.float32)
        logger.info("向量化完成: %d 条, 耗时 %.1fs", len(emb), time.time() - t0)

        np.save(index_dir / EMB_FILE, emb)
        df[META_COLS].to_parquet(index_dir / META_FILE, index=False)
        (index_dir / INDEX_META_FILE).write_text(
            json.dumps({
                "n_docs": int(len(emb)),
                "dim": int(emb.shape[1]),
                "metric": "cosine",
                "embedding_model": "BAAI/bge-m3",
                "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("索引落盘: %s (%d 条 × %d 维)", index_dir, len(emb), emb.shape[1])
        return cls(index_dir)

    # ------------------------------------------------------------------
    #  增量更新
    # ------------------------------------------------------------------

    @classmethod
    def add_documents(
        cls,
        news_df: pd.DataFrame,
        model,
        index_dir: Path,
        batch_size: int = 64,
    ) -> int:
        """增量追加新新闻（按 id 幂等去重, 原子落盘）。

        流程: 加载现有索引 → 按 md5(url) 去重 → 仅编码新增文档 →
              向量/元数据分别拼接 → 临时文件写入后 os.replace 原子替换。

        Args:
            news_df: 新新闻 DataFrame（列要求同 build）。
            model: SentenceTransformer 实例。
            index_dir: 已存在的索引目录（须先 build 过）。
            batch_size: 编码批次大小。

        Returns:
            实际新增文档数（去重后）。
        """
        import hashlib
        import os
        import time

        index_dir = Path(index_dir)
        store = cls(index_dir, use_gpu=False)  # 载入现有索引(编码本身用GPU, 无需常驻)

        df = news_df.reset_index(drop=True).copy()
        if "id" not in df.columns or df["id"].isna().any():
            df["id"] = [
                "news_" + hashlib.md5(str(u).encode("utf-8")).hexdigest()[:16]
                for u in df.get("url", pd.Series([""] * len(df)))
            ]
        # 幂等去重: 跳过库内已有 id
        existing = set(store.meta["id"].astype(str))
        df = df[~df["id"].astype(str).isin(existing)]
        if len(df) == 0:
            logger.info("增量更新: 0 条新增(全部已存在)")
            return 0

        texts = (df["title"].astype(str) + " " + df["text"].astype(str)).tolist()
        t0 = time.time()
        new_emb = np.asarray(model.encode(
            texts, batch_size=batch_size, normalize_embeddings=True,
            show_progress_bar=True, convert_to_numpy=True,
        ), dtype=np.float32)
        logger.info("增量向量化: %d 条, %.1fs", len(new_emb), time.time() - t0)

        emb_all = np.vstack([store.embeddings, new_emb])
        meta_all = pd.concat([store.meta, df[META_COLS]], ignore_index=True)

        # 原子落盘: 先写临时文件再替换(防中途崩溃损坏索引)
        # 注意 np.save 会自动补 .npy 后缀, 临时文件名须以 .npy 结尾
        tmp_emb = index_dir / "embeddings.tmp.npy"
        tmp_meta = index_dir / (META_FILE + ".tmp")
        np.save(tmp_emb, emb_all)
        meta_all.to_parquet(tmp_meta, index=False)
        os.replace(tmp_emb, index_dir / EMB_FILE)
        os.replace(tmp_meta, index_dir / META_FILE)

        (index_dir / INDEX_META_FILE).write_text(
            json.dumps({
                **store.index_meta,
                "n_docs": int(emb_all.shape[0]),
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "last_increment": int(len(new_emb)),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(
            "增量更新落盘: 新增 %d 条, 总计 %d 条",
            len(new_emb), emb_all.shape[0],
        )
        return int(len(new_emb))
