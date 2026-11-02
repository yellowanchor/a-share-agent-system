# -*- coding: utf-8 -*-
"""run_agent_demo.py — 三 Agent 编排工作流端到端演示

Phase 3.2 起串联逻辑由 `Orchestrator` 承担：Analyst 只跑一次，
Strategist → Critic 构成带反馈的迭代闭环（Critic 不通过则重试）。

用法:
    # 离线联调（不加载模型，秒级完成，验证流程与降级路径）
    python scripts/run_agent_demo.py --code 600519 --backend mock

    # 真实推理（Qwen2.5-7B-Instruct 4bit + bge-m3，首次加载约 1-2 分钟）
    python scripts/run_agent_demo.py --code 600519 --backend qwen

    # 指定截止日与检索词（Point-in-Time 对齐）
    python scripts/run_agent_demo.py --code 600519 --backend qwen \
        --as-of 2026-08-20 --query "业绩 增长 白酒"

    # 关闭闭环（等价于 Phase 3.1 的单次单向流水线，用于对照实验）
    python scripts/run_agent_demo.py --code 600519 --backend qwen --max-retries 0

输出: data/processed/agent_report_<code>_<timestamp>.md
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# 支持直接 python scripts/xxx.py 运行（无需 -m）
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agents import CriticAgent, Orchestrator, OrchestratorConfig  # noqa: E402
from src.agents.report import LABEL_DIRECTION, LABEL_SENTIMENT, build_markdown  # noqa: E402
from src.models.llm import get_backend  # noqa: E402
from src.utils.logger import setup_logging  # noqa: E402


def main() -> int:
    """主流程：解析参数 → 建后端 → 编排层执行 → 输出报告。"""
    ap = argparse.ArgumentParser(description="三 Agent 编排工作流演示")
    ap.add_argument("--code", default="600519", help="股票代码（6 位）")
    ap.add_argument("--query", default=None, help="检索查询，默认自动生成")
    ap.add_argument("--as-of", dest="as_of", default=None,
                    help="数据截止日 YYYY-MM-DD（Point-in-Time 对齐）")
    ap.add_argument("--backend", choices=["mock", "qwen"], default="mock",
                    help="mock=离线桩（默认），qwen=真实推理")
    ap.add_argument("--top-k", type=int, default=8, help="检索证据条数")
    ap.add_argument("--max-retries", type=int, default=2,
                    help="Critic 不通过时的最大重试次数（0=关闭闭环）")
    ap.add_argument("--no-llm-critic", action="store_true",
                    help="Critic 只跑规则层，不做大模型语义校验")
    ap.add_argument("--out", default=None, help="报告输出路径（默认 data/processed/）")
    args = ap.parse_args()

    setup_logging(level="INFO", log_file=str(ROOT / "logs" / "system.log"))

    print(f"=== 三 Agent 编排工作流 | 代码={args.code} 后端={args.backend} "
          f"重试上限={args.max_retries} ===")
    llm = get_backend(args.backend)

    # Critic 默认启用语义层；--no-llm-critic 时关掉（批量评测常用，省一半耗时）
    critic = CriticAgent(llm=llm, use_llm=not args.no_llm_critic)
    orch = Orchestrator(
        llm=llm,
        config=OrchestratorConfig(max_retries=args.max_retries),
        critic=critic,
    )

    out = orch.run({"stock_code": args.code, "query": args.query,
                    "as_of": args.as_of, "top_k": args.top_k})
    result = out["result"]
    report, advice, verdict = result.report, result.advice, result.verdict

    print(f"[Analyst]    证据 {report.n_evidence} 条, 情感 "
          f"{LABEL_SENTIMENT.get(report.sentiment, report.sentiment)}, "
          f"LLM={report.llm_used} ({result.timings.get('Analyst', 0):.0f}ms)")
    for r in result.rounds:
        errs = "、".join(r.error_codes) or "无"
        print(f"[Round {r.round}]     方向 {r.advice.direction}, "
              f"仓位 {r.advice.position_pct:.1f}%, 通过={r.verdict.passed}, "
              f"阻断={errs} ({r.elapsed_ms:.0f}ms)")
    print(f"[Final]      终局={LABEL_DIRECTION.get(advice.direction, advice.direction)} "
          f"{advice.position_pct:.1f}%, 来源={result.final_source}, "
          f"通过={verdict.passed} ({result.timings.get('Total', 0):.0f}ms)")
    for i in verdict.issues:
        print(f"    - [{i.severity}] {i.code} {i.message}")

    # 报告落盘
    timings = dict(result.timings)
    timings["backend"] = args.backend
    md = build_markdown(report, advice, verdict, timings, result=result)
    if args.out:
        path = Path(args.out)
    else:
        path = (ROOT / "data" / "processed"
                / f"agent_report_{args.code}_{time.strftime('%m%d_%H%M%S')}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(md, encoding="utf-8")
    print(f"\n报告已保存: {path}")

    if hasattr(llm, "unload"):
        llm.unload()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
