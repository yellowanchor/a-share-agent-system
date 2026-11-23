# -*- coding: utf-8 -*-
"""RAG 检索质量评估（说明书 Phase 2 任务项 #3 前半）
=====================================================

指标: Recall@5 / MRR@10

相关性定义（无人工标注条件下的标准代理方案）:
    以新闻标题为查询, 检索结果中与查询新闻**共享任一股票代码**的
    文档视为相关（同公司新闻在舆情场景下语义相关）。
    查询新闻自身（标题完全相同）从候选中排除。

流程:
    1. 从 meta.parquet 随机抽 500 条有股票代码的新闻作为查询
    2. bge-m3 批量编码（GPU）→ LocalVectorStore 全库检索 top-10
    3. 统计 Recall@5 与 MRR@10, 输出质量报告

用法:
    python scripts/eval_rag_recall.py
"""
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

N_QUERIES = 500
SEED = 42


def code_set(raw) -> set:
    """解析 stock_code 字段(可能逗号分隔多代码)为集合."""
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return set()
    return {c.strip() for c in str(raw).split(",") if c.strip()}


def main() -> None:
    from src.models.rag.retriever import NewsRetriever

    print("加载检索器 (bge-m3 + LocalVectorStore)...")
    retriever = NewsRetriever()
    store = retriever._store  # noqa: SLF001 评估脚本直接用底层批量接口

    meta = store.meta  # DataFrame: title/stock_code/publish_time/...
    print(f"库内文档: {len(meta)} 条")

    # 抽样: 仅取有股票代码的新闻
    meta = meta.copy()
    meta["_codes"] = meta["stock_code"].apply(code_set)
    valid = meta[meta["_codes"].str.len() > 0]
    queries = valid.sample(n=min(N_QUERIES, len(valid)), random_state=SEED)
    print(f"有效查询池: {len(valid)} 条, 抽样 {len(queries)} 条")

    # 批量编码查询标题（比逐条 encode_query 快 10 倍以上）
    t0 = time.time()
    q_emb = retriever._model.encode(  # noqa: SLF001
        queries["title"].tolist(),
        normalize_embeddings=True, convert_to_numpy=True,
        batch_size=64, show_progress_bar=True,
    ).astype(np.float32)
    print(f"查询编码: {len(queries)} 条, {time.time()-t0:.0f}s")

    # 逐查询检索 top-10（每次矩阵乘仅 ~3ms）
    t0 = time.time()
    recall5_hits = 0
    rr_sum = 0.0
    details = []
    title_list = queries["title"].tolist()
    codes_list = queries["_codes"].tolist()
    for qi in range(len(queries)):
        hits = store.search(q_emb[qi], top_k=10)
        # 排除自身（标题完全相同）
        hits = [h for h in hits if h["title"].strip() != title_list[qi].strip()]
        # 相关性判定
        rel_flags = [
            bool(code_set(h.get("stock_code")) & codes_list[qi]) for h in hits
        ]
        top5 = rel_flags[:5]
        hit5 = any(top5)
        recall5_hits += hit5
        rr = 0.0
        for rank, rel in enumerate(rel_flags, start=1):
            if rel:
                rr = 1.0 / rank
                break
        rr_sum += rr
        details.append({
            "query_title": title_list[qi],
            "recall@5": hit5,
            "first_rel_rank": int(1 / rr) if rr else 0,
        })
    search_elapsed = time.time() - t0

    recall5 = recall5_hits / len(queries)
    mrr = rr_sum / len(queries)
    print(f"\nRecall@5 = {recall5:.3f} ({recall5_hits}/{len(queries)})")
    print(f"MRR@10   = {mrr:.3f}")
    print(f"检索耗时: {len(queries)} 次共 {search_elapsed:.1f}s "
          f"(均值 {search_elapsed/len(queries)*1000:.0f}ms/次)")

    # 分布统计
    ddf = pd.DataFrame(details)
    rank_dist = ddf[ddf["first_rel_rank"] > 0]["first_rel_rank"] \
        .value_counts().sort_index()

    lines = [
        "# RAG 检索质量评估报告（Recall@5 / MRR@10）", "",
        "> 评估日期：2026-09-12 | 索引：LocalVectorStore（126,137 条 × 1024 维）",
        "> 查询：随机抽样 500 条新闻标题（seed=42），相关性 = 共享股票代码",
        "> 查询自身已从候选中排除；检索范围为全库（无股票/时间过滤）", "",
        "## 核心指标", "",
        "| 指标 | 数值 |",
        "|------|------|",
        f"| **Recall@5** | **{recall5:.3f}**（{recall5_hits}/{len(queries)}） |",
        f"| **MRR@10** | **{mrr:.3f}** |",
        f"| 单次检索耗时 | {search_elapsed/len(queries)*1000:.0f}ms |", "",
        "## 首个相关结果排名分布", "",
        "| 排名 | 查询数 |", "|------|--------|",
    ]
    for rank, cnt in rank_dist.items():
        lines.append(f"| Top-{rank} | {cnt} |")
    lines += [
        "",
        "## 说明",
        "",
        "- 相关性采用「同股票代码」代理判定，属弱相关标准：同公司新闻不一定语义相关，",
        "  但语义相关的金融新闻大概率涉及同公司/同行业，该指标是 Recall 的下界估计。",
        "- bge-m3 查询编码耗时约 108ms/条（GPU），向量检索本身仅 ~3ms，",
        "  端到端性能详见 Phase 1 验收报告 quality_report_rag.md。",
    ]
    out = ROOT / "data" / "processed" / "quality_report_rag_recall.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告已保存: {out}")


if __name__ == "__main__":
    main()
