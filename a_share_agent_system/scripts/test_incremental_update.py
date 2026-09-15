# -*- coding: utf-8 -*-
"""增量更新机制验证脚本（在索引副本上测试, 不动生产索引）.

验证项:
    1. 幂等去重: 重复提交库内已有新闻 → 新增 0 条
    2. 增量追加: 提交 50 条新新闻 → 新增 50 条, count 增加
    3. 检索可达: 新文档能被语义检索命中
    4. 落盘一致性: 重新加载后 count 不变、向量/元数据对齐
"""
import shutil
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.models.rag.indexer import VectorIndexer  # noqa: E402
from src.models.rag.local_store import LocalVectorStore  # noqa: E402

SRC_INDEX = ROOT / "data" / "vector_store"


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="vs_incr_test_"))
    try:
        # 1. 复制生产索引到临时目录
        for f in ["embeddings.npy", "meta.parquet", "index_meta.json"]:
            shutil.copy2(SRC_INDEX / f, tmp / f)
        store0 = LocalVectorStore(tmp, use_gpu=False)
        n0 = store0.count
        print(f"[准备] 副本索引: {n0} 条 -> {tmp}")

        indexer = VectorIndexer(vector_store_dir=str(tmp))

        # 2. 幂等测试: 取库内 100 条重新提交
        existing = store0.meta.head(100).to_dict("records")
        added = indexer.incremental_update(existing)
        assert added == 0, f"幂等测试失败: 期望 0, 实际 {added}"
        print(f"[通过] 幂等去重: 100 条重复新闻 -> 新增 {added}")

        # 3. 增量测试: 构造 50 条全新新闻(新 URL → 新 id)
        fresh = []
        for i in range(50):
            fresh.append({
                "title": f"测试新闻: 某公司第{i}号重大利好公告 净利润大幅增长",
                "text": f"这是增量更新验证用的第 {i} 条合成新闻, 内容关于业绩预增与订单签约。",
                "source": "增量测试",
                "stock_code": "999999",
                "publish_time": "2026-09-12 12:00:00",
                "url": f"http://incremental-test.local/news/{i}",
            })
        added = indexer.incremental_update(fresh)
        assert added == 50, f"增量测试失败: 期望 50, 实际 {added}"
        print(f"[通过] 增量追加: 50 条新新闻 -> 新增 {added}")

        # 4. 重新加载验证落盘一致性
        store1 = LocalVectorStore(tmp, use_gpu=False)
        assert store1.count == n0 + 50, \
            f"落盘不一致: 期望 {n0+50}, 实际 {store1.count}"
        assert len(store1.meta) == store1.embeddings.shape[0]
        print(f"[通过] 落盘一致: {n0} -> {store1.count}, 向量/元数据对齐")

        # 5. 检索可达: 用新文档相关查询应命中新文档
        from src.models.rag.retriever import NewsRetriever
        r = NewsRetriever(index_dir=tmp)
        hits = r.search("重大利好 净利润增长 订单签约", stock_code="999999", top_k=3)
        assert hits and all("测试新闻" in h["title"] for h in hits), \
            f"检索可达性失败: {hits[:1]}"
        print(f"[通过] 检索可达: top-3 全部命中新文档 "
              f"(score={hits[0]['score']:.3f})")

        # 6. 再次幂等: 重提交这 50 条应为 0
        added2 = indexer.incremental_update(fresh)
        assert added2 == 0, f"二次幂等失败: {added2}"
        print(f"[通过] 二次幂等: 重提交 50 条 -> 新增 {added2}")

        print("\n==== 增量更新机制验证 5/5 全部通过 ====")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        print(f"[清理] 临时目录已删除")


if __name__ == "__main__":
    main()
