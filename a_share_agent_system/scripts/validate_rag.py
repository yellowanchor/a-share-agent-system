# -*- coding: utf-8 -*-
"""
validate_rag.py — RAG 索引端到端验证与验收报告生成
====================================================
验证 ChromaDB 新闻向量索引 + DuckDB 行情/财务数据链路：

    1. 检索器加载与 collection 完整性
    2. 语义检索（全市场 / 按股票过滤 / 时间过滤）
    3. 结构化查询（按股票取最近新闻）
    4. 性能指标（查询延迟）
    5. 与行情/财务数据联动（Agent 场景模拟）

用法:
    python validate_rag.py [--report-dir DIR]

输出:
    quality_report_rag.md — RAG 索引验收报告
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("validate_rag")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.models.rag.retriever import NewsRetriever  # noqa: E402
from src.data.storage.market_store import MarketStore  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG 索引端到端验证")
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=ROOT / "data" / "processed",
        help="验收报告输出目录",
    )
    args = parser.parse_args()
    args.report_dir.mkdir(parents=True, exist_ok=True)

    report: dict = {"checks": [], "results": {}}

    # ------------------------------------------------------------------
    # 1. 检索器加载与完整性
    # ------------------------------------------------------------------
    t0 = time.time()
    retriever = NewsRetriever()
    load_time = time.time() - t0
    count = retriever.count
    report["results"]["collection"] = {"documents": count, "load_time_s": round(load_time, 2)}
    report["checks"].append({
        "name": "检索器加载",
        "status": "OK" if count == 126137 else "WARN",
        "detail": f"collection=news_articles, 文档数={count:,} (预期 126,137), 加载 {load_time:.1f}s",
    })

    # ------------------------------------------------------------------
    # 2. 语义检索
    # ------------------------------------------------------------------
    cases = [
        ("全市场语义检索", "央行降准 释放流动性 利好 A股", None, None),
        ("按股票过滤", "业绩增长 净利润 提升", "600519", None),        # 贵州茅台
        ("按股票+时间过滤", "回购 增持", "600519", "2026-06-01 00:00:00"),
        ("行业主题检索", "新能源汽车 销量 创新高", None, None),
    ]
    for name, q, code, start in cases:
        t0 = time.time()
        hits = retriever.search(q, stock_code=code, start=start, top_k=5)
        lat = (time.time() - t0) * 1000
        top = hits[0] if hits else {}
        report["results"][f"search:{name}"] = {
            "query": q, "stock": code, "hits": len(hits), "latency_ms": round(lat, 1),
            "top_title": top.get("title", "")[:60],
            "top_score": top.get("score"),
            "top_stock": top.get("stock_code"),
        }
        report["checks"].append({
            "name": f"语义检索 · {name}",
            "status": "OK" if hits else "FAIL",
            "detail": (
                f"查询「{q}」→ {len(hits)} 条, 延迟 {lat:.0f}ms"
                + (f", 最相关: [{top.get('score')}] {top.get('title','')[:40]}" if hits else "")
            ),
        })

    # ------------------------------------------------------------------
    # 3. 结构化查询（按股票取最近新闻）
    # ------------------------------------------------------------------
    t0 = time.time()
    recent = retriever.recent_by_stock("600519", limit=5)
    lat = (time.time() - t0) * 1000
    report["results"]["recent:600519"] = {
        "hits": len(recent), "latency_ms": round(lat, 1),
        "titles": [r["title"][:40] for r in recent[:3]],
    }
    report["checks"].append({
        "name": "结构化查询 · 600519 最近新闻",
        "status": "OK" if recent else "FAIL",
        "detail": f"取到 {len(recent)} 条, 延迟 {lat:.0f}ms",
    })

    # ------------------------------------------------------------------
    # 4. 性能基准（连续 20 次查询，拆分编码/检索耗时）
    # ------------------------------------------------------------------
    latencies, enc_times, sea_times = [], [], []
    for _ in range(20):
        t0 = time.time()
        qvec = retriever.encode_query("A股 市场 走势 分析")
        t1 = time.time()
        retriever._store.search(qvec, top_k=5)
        t2 = time.time()
        latencies.append((t2 - t0) * 1000)
        enc_times.append((t1 - t0) * 1000)
        sea_times.append((t2 - t1) * 1000)
    avg = sum(latencies) / len(latencies)
    p95 = sorted(latencies)[int(len(latencies) * 0.95) - 1]
    enc_avg = sum(enc_times) / len(enc_times)
    sea_avg = sum(sea_times) / len(sea_times)
    report["results"]["performance"] = {
        "avg_ms": round(avg, 1), "p95_ms": round(p95, 1), "runs": len(latencies),
        "encode_avg_ms": round(enc_avg, 1), "search_avg_ms": round(sea_avg, 1),
    }
    report["checks"].append({
        "name": "性能基准 (20 次查询)",
        "status": "OK" if p95 < 250 else "WARN",
        "detail": (
            f"端到端平均 {avg:.0f}ms (P95 {p95:.0f}ms, 阈值 250ms); "
            f"其中查询编码 {enc_avg:.0f}ms + 向量检索 {sea_avg:.0f}ms"
        ),
    })

    # ------------------------------------------------------------------
    # 5. 行情 + 财务 + 新闻 联动（Agent 场景模拟）
    # ------------------------------------------------------------------
    store = MarketStore()
    m = store.daily_range("sh.600519", "2026-08-10", "2026-08-20")
    f = store.latest_financial("sh.600519")
    n = retriever.recent_by_stock("600519", limit=3)
    agent_demo = {
        "stock": "600519 贵州茅台",
        "market_rows": len(m),
        "latest_close": float(m.iloc[-1]["close"]) if len(m) else None,
        "latest_report_date": str(f.iloc[0]["report_date"]) if len(f) else None,
        "latest_eps": float(f.iloc[0]["eps"]) if len(f) else None,
        "news_titles": [x["title"][:40] for x in n[:2]],
    }
    report["results"]["agent_scenario"] = agent_demo
    report["checks"].append({
        "name": "Agent 数据联动场景",
        "status": "OK" if len(m) and len(f) and n else "FAIL",
        "detail": f"行情 {len(m)} 行 + 最新财务 {agent_demo['latest_report_date']} + 新闻 {len(n)} 条",
    })

    # ------------------------------------------------------------------
    # 汇总 + 写报告
    # ------------------------------------------------------------------
    passed = sum(1 for c in report["checks"] if c["status"] == "OK")
    total = len(report["checks"])
    report["summary"] = {"passed": passed, "total": total, "ok": passed == total}

    out_md = args.report_dir / "quality_report_rag.md"
    lines = [
        "# RAG 索引验收报告",
        "",
        f"- 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 数据源: data/processed/news.parquet (126,137 条)",
        f"- 向量库: data/vector_store (LocalVectorStore, numpy 精确余弦检索)",
        f"- 嵌入模型: BAAI/bge-m3 (1024 维, GPU)",
        "",
        f"## 总体结论: **{'✅ 全部通过' if report['summary']['ok'] else '⚠️ 部分未通过'}** ({passed}/{total})",
        "",
        "## 检查项明细",
        "",
        "| # | 检查项 | 状态 | 详情 |",
        "|---|--------|------|------|",
    ]
    for i, c in enumerate(report["checks"], 1):
        lines.append(f"| {i} | {c['name']} | {c['status']} | {c['detail']} |")

    lines += [
        "",
        "## 检索质量示例",
        "",
        "### 案例 1：全市场语义检索",
        "",
        "查询：央行降准 释放流动性 利好 A股",
        "",
        "| 相似度 | 标题 | 股票 | 时间 |",
        "|--------|------|------|------|",
    ]
    for r in retriever.search("央行降准 释放流动性 利好 A股", top_k=5):
        lines.append(
            f"| {r['score']} | {r['title'][:50]} | {r['stock_code']} | {r['publish_time'][:16]} |"
        )

    lines += ["", "### 案例 2：按股票过滤（600519 贵州茅台 · 业绩增长）", "", "| 相似度 | 标题 | 时间 |", "|--------|------|------|"]
    for r in retriever.search("业绩增长 净利润 提升", stock_code="600519", top_k=5):
        lines.append(f"| {r['score']} | {r['title'][:50]} | {r['publish_time'][:16]} |")

    lines += [
        "",
        "## Agent 场景联动数据",
        "",
        "```json",
        json.dumps(agent_demo, ensure_ascii=False, indent=2),
        "```",
        "",
        "## 性能指标",
        "",
        f"- 检索平均延迟: {report['results']['performance']['avg_ms']} ms"
        f"（编码 {report['results']['performance']['encode_avg_ms']} ms + "
        f"向量检索 {report['results']['performance']['search_avg_ms']} ms）",
        f"- 检索 P95 延迟: {report['results']['performance']['p95_ms']} ms",
        f"- collection 加载: {report['results']['collection']['load_time_s']} s",
        "",
        "## 使用方法",
        "",
        "```python",
        "from src.models.rag.retriever import NewsRetriever",
        "from src.data.storage.market_store import MarketStore",
        "",
        "retriever = NewsRetriever()  # 加载向量检索器",
        "# 语义检索（支持股票/时间过滤）",
        "hits = retriever.search(\"贵州茅台 业绩增长\", stock_code=\"600519\", top_k=5)",
        "",
        "store = MarketStore()        # 加载行情/财务",
        "df = store.daily_range(\"sh.600519\", \"2026-08-01\", \"2026-08-20\")",
        "fin = store.latest_financial(\"sh.600519\")",
        "```",
        "",
    ]
    out_md.write_text("\n".join(lines), encoding="utf-8")

    # JSON 备份
    (args.report_dir / "quality_report_rag.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"=== 验收结果: {passed}/{total} 通过 ===")
    for c in report["checks"]:
        print(f"[{c['status']}] {c['name']}: {c['detail']}")
    print(f"\n报告已写入: {out_md}")


if __name__ == "__main__":
    main()
