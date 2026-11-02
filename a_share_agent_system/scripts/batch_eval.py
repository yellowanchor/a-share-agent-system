# -*- coding: utf-8 -*-
"""batch_eval.py — 多股票批量评测（Phase 3.2）

单只股票的演示说明不了系统的稳定性：一批股票跑下来才有「首轮通过率」
「闭环收敛增益」「规则触发频次」这些可写进论文的量化指标。

本脚本做三件事
--------------
1. 复用同一批 Agent 实例批量执行（bge-m3 / Qwen 只加载一次）
2. 汇总统计（src/agents/stats.py）并渲染 Markdown 报告
3. 同时落盘结构化 JSON，供论文图表与 Phase 3.3 前端复用

用法:
    # 真实推理，默认 8 只跨行业股票
    python scripts/batch_eval.py --backend qwen --as-of 2026-08-20

    # 离线联调（秒级，验证流程与统计逻辑）
    python scripts/batch_eval.py --backend mock

    # 自定义股票池 / 关闭闭环做对照实验
    python scripts/batch_eval.py --codes 600519,600030,600276 --max-retries 0

输出:
    data/processed/batch_eval_<timestamp>.md     评测报告
    data/processed/batch_eval_<timestamp>.json   结构化结果
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agents import CriticAgent, Orchestrator, OrchestratorConfig  # noqa: E402
from src.agents.report import (  # noqa: E402
    LABEL_DIRECTION,
    LABEL_SENTIMENT,
    LABEL_SOURCE,
    build_batch_markdown,
)
from src.agents.stats import summarize_results  # noqa: E402
from src.models.llm import get_backend  # noqa: E402
from src.utils.logger import setup_logging  # noqa: E402

#: 默认评测池：8 只跨行业、新闻覆盖均 >100 条的大市值标的
#: （括号内为行业，仅便于人工阅读，不参与计算）
DEFAULT_POOL: List[str] = [
    "600519",   # 贵州茅台（白酒）
    "600030",   # 中信证券（证券）
    "600000",   # 浦发银行（银行）
    "600276",   # 恒瑞医药（化学制药）
    "600028",   # 中国石化（炼化及贸易）
    "600104",   # 上汽集团（乘用车）
    "601933",   # 永辉超市（一般零售）
    "600438",   # 通威股份（光伏设备）
]


def load_top_news_codes(limit: int) -> List[str]:
    """按新闻覆盖量取前 N 只股票（用于 --pool top）。"""
    import duckdb

    db = ROOT / "data" / "processed" / "market_data.duckdb"
    if not db.exists():
        print(f"[警告] 数据库不存在 {db}，回退默认池")
        return DEFAULT_POOL[:limit]
    con = duckdb.connect(str(db), read_only=True)
    rows = con.execute(
        "SELECT stock_code, COUNT(*) n FROM news_articles "
        "WHERE stock_code IS NOT NULL AND stock_code <> '' "
        "GROUP BY stock_code ORDER BY n DESC LIMIT ?", [limit]
    ).fetchall()
    con.close()
    return [r[0] for r in rows]


def stock_names(codes: List[str]) -> Dict[str, str]:
    """批量取股票简称（仅用于报告可读性；失败时返回空字典）。"""
    try:
        import duckdb

        db = ROOT / "data" / "processed" / "market_data.duckdb"
        if not db.exists():
            return {}
        con = duckdb.connect(str(db), read_only=True)
        out: Dict[str, str] = {}
        for c in codes:
            row = con.execute(
                "SELECT name FROM financials_yjbb WHERE code = ? "
                "ORDER BY pub_date DESC LIMIT 1", [c]
            ).fetchone()
            if row and row[0]:
                out[c] = str(row[0])
        con.close()
        return out
    except Exception:
        return {}


def main() -> int:
    """主流程：解析参数 → 批量执行 → 统计 → 落盘。"""
    ap = argparse.ArgumentParser(description="多股票批量评测")
    ap.add_argument("--codes", default=None,
                    help="股票代码，逗号分隔（优先于 --pool）")
    ap.add_argument("--pool", choices=["default", "top"], default="default",
                    help="default=8 只跨行业池；top=新闻覆盖最高的 N 只")
    ap.add_argument("--limit", type=int, default=8, help="--pool top 时的取用数量")
    ap.add_argument("--as-of", dest="as_of", default=None,
                    help="数据截止日 YYYY-MM-DD（Point-in-Time 对齐）")
    ap.add_argument("--backend", choices=["mock", "qwen"], default="mock",
                    help="mock=离线桩（默认），qwen=真实推理")
    ap.add_argument("--max-retries", type=int, default=2,
                    help="Critic 不通过时的最大重试次数（0=关闭闭环，用于对照）")
    ap.add_argument("--top-k", type=int, default=8, help="检索证据条数")
    ap.add_argument("--max-position-pct", type=float, default=20.0,
                    help="单票仓位上限（%%），同时作用于生成与校验")
    ap.add_argument("--risk-position-pct", type=float, default=None,
                    help="仅收紧 Critic 校验上限（生成侧不变）。模拟『风控标准"
                         "比策略生成更严』的真实场景，用于触发 P001 观察闭环收敛")
    ap.add_argument("--no-llm-critic", action="store_true",
                    help="Critic 只跑规则层（批量评测常用，显著省时）")
    ap.add_argument("--out-dir", default=None, help="输出目录（默认 data/processed/）")
    args = ap.parse_args()

    setup_logging(level="INFO", log_file=str(ROOT / "logs" / "system.log"))

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    elif args.pool == "top":
        codes = load_top_news_codes(args.limit)
    else:
        codes = DEFAULT_POOL[:args.limit] if args.limit != 8 else list(DEFAULT_POOL)

    names = stock_names(codes)
    print(f"=== 批量评测 | {len(codes)} 只 | 后端={args.backend} | "
          f"截止日={args.as_of or '最新'} | 重试上限={args.max_retries} ===")
    for c in codes:
        print(f"    {c} {names.get(c, '')}")

    llm = get_backend(args.backend)
    # 风控收紧实验：--risk-position-pct 只压 Critic 的判定标准，
    # Strategist 仍按 --max-position-pct 生成（首轮会触发 P001 → 反馈重试）
    critic_limit = (args.risk_position_pct if args.risk_position_pct is not None
                    else args.max_position_pct)
    critic = CriticAgent(llm=llm, use_llm=not args.no_llm_critic,
                        max_position_pct=critic_limit)
    from src.agents import StrategistAgent
    strategist = StrategistAgent(llm=llm,
                                 max_position_pct=args.max_position_pct)
    orch = Orchestrator(
        llm=llm,
        config=OrchestratorConfig(max_retries=args.max_retries),
        critic=critic,
        strategist=strategist,
    )

    def on_progress(i: int, n: int, res) -> None:
        """每只完成后的进度输出。"""
        rep, adv = res.report, res.advice
        errs = "、".join(x.code for x in res.verdict.issues
                        if x.severity == "error") or "无"
        print(f"  [{i}/{n}] {res.stock_code} {names.get(res.stock_code, '')} "
              f"情感={LABEL_SENTIMENT.get(rep.sentiment, rep.sentiment)} "
              f"证据={rep.n_evidence} 轮次={res.n_rounds} "
              f"首轮={'✅' if res.first_pass else '❌'} "
              f"终局={'✅' if res.final_pass else '❌'} "
              f"({LABEL_SOURCE.get(res.final_source, res.final_source)}) "
              f"方向={LABEL_DIRECTION.get(adv.direction, adv.direction)} "
              f"仓位={adv.position_pct:.1f}% 阻断={errs}")

    t0 = time.time()
    results = orch.run_batch(codes, as_of=args.as_of, top_k=args.top_k,
                             on_progress=on_progress)
    wall_ms = (time.time() - t0) * 1000

    stats = summarize_results(results)
    meta = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "backend": args.backend,
        "as_of": args.as_of,
        "max_retries": args.max_retries,
        "top_k": args.top_k,
        "llm_critic": not args.no_llm_critic,
        "wall_clock_ms": round(wall_ms, 1),
        "codes": codes,
        "names": names,
        "max_position_pct": args.max_position_pct,
        "risk_position_pct": critic_limit,
    }

    md = build_batch_markdown(results, meta, stats)
    out_dir = Path(args.out_dir) if args.out_dir else ROOT / "data" / "processed"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%m%d_%H%M%S")

    md_path = out_dir / f"batch_eval_{stamp}.md"
    md_path.write_text(md, encoding="utf-8")

    json_path = out_dir / f"batch_eval_{stamp}.json"
    payload = {
        "meta": meta,
        "stats": stats,
        "results": [r.to_json_dict() for r in results],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                         encoding="utf-8")

    print(f"\n--- 总体指标 ---")
    print(f"  首轮通过率 {stats.get('first_pass_rate', 0):.1%} → "
          f"终局通过率 {stats.get('final_pass_rate', 0):.1%} "
          f"（闭环增益 {stats.get('retry_gain', 0):+.1%}）")
    print(f"  平均轮次 {stats.get('avg_rounds', 0)}　"
          f"平均证据 {stats.get('avg_evidence', 0)} 条　"
          f"单只平均耗时 {stats.get('avg_total_ms', 0):.0f}ms")
    print(f"  终局来源分布: {stats.get('source_dist', {})}")
    print(f"\n报告: {md_path}")
    print(f"数据: {json_path}")

    if hasattr(llm, "unload"):
        llm.unload()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
