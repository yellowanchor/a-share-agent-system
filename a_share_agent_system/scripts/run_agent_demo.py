# -*- coding: utf-8 -*-
"""run_agent_demo.py — 三 Agent 工作流端到端演示

串联 Analyst → Strategist → Critic，输出一份带证据链的 Markdown 报告。

用法:
    # 离线联调（不加载模型，秒级完成，验证流程与降级路径）
    python scripts/run_agent_demo.py --code 600519 --backend mock

    # 真实推理（Qwen2.5-7B-Instruct 4bit + bge-m3，首次加载约 1-2 分钟）
    python scripts/run_agent_demo.py --code 600519 --backend qwen

    # 指定截止日与检索词（Point-in-Time 对齐）
    python scripts/run_agent_demo.py --code 600519 --backend qwen \
        --as-of 2026-08-20 --query "业绩 增长 白酒"

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

from src.agents import AnalystAgent, CriticAgent, StrategistAgent  # noqa: E402
from src.models.llm import get_backend  # noqa: E402
from src.utils.logger import setup_logging  # noqa: E402

LABEL_SENTIMENT = {"positive": "利好", "negative": "利空", "neutral": "中性"}
LABEL_DIRECTION = {"long": "做多", "short": "做空", "watch": "观望"}
LABEL_IMPACT = {"major": "重大", "moderate": "一般", "minor": "轻微"}


def build_markdown(report, advice, verdict, timings: dict) -> str:
    """把三 Agent 结果渲染为 Markdown 报告。"""
    lines: list = [
        f"# {report.stock_code} 舆情与策略分析报告（三 Agent 协作）",
        "",
        f"- 数据截止日：{report.as_of or '未指定'}",
        f"- 检索查询：{report.query}",
        f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- LLM 后端：{timings.get('backend', 'mock')}"
        f"（Analyst={report.llm_used} / Strategist={advice.llm_used} / "
        f"Critic语义校验={verdict.llm_used}）",
        "",
        "## 一、舆情研判（Analyst）",
        "",
        report.summary or "（无综述）",
        "",
        "| 情感极性 | 置信度 | 影响程度 | 关键主题 | 结论有效期 |",
        "|---|---|---|---|---|",
        f"| {LABEL_SENTIMENT.get(report.sentiment, report.sentiment)} "
        f"| {report.confidence:.2f} "
        f"| {LABEL_IMPACT.get(report.impact, report.impact)} "
        f"| {'、'.join(report.themes) or '—'} | {report.valid_until or '—'} |",
        "",
        f"- 研判实际引用的证据编号："
        f"{'、'.join(f'[证据 {i}]' for i in report.evidence_ids) or '未引用'}",
        "",
        "### 证据链（RAG 检索结果，可溯源）",
        "",
        "| 编号 | 发布时间 | 来源 | 相似度 | 情感 | 标题 |",
        "|---|---|---|---|---|---|",
    ]
    for e in report.evidence:
        score = f"{e.score:.3f}" if e.score is not None else "—"
        lines.append(
            f"| {e.idx} | {e.publish_time} | {e.source} | {score} "
            f"| {LABEL_SENTIMENT.get(e.sentiment, e.sentiment)} | {e.title} |"
        )

    lines += [
        "",
        "### 量价快照",
        "",
        "| 指标 | 数值 | 指标 | 数值 |",
        "|---|---|---|---|",
    ]
    snap = report.market_snapshot or {}
    pairs = [
        ("close", "最新收盘"), ("ma20", "MA20"), ("ma60", "MA60"),
        ("rsi14", "RSI14"), ("atr14", "ATR14"), ("ret_20d", "近20日涨跌%"),
        ("vol_ratio", "量比"), ("annual_vol", "年化波动率"),
        ("support", "支撑"), ("resistance", "阻力"),
    ]
    for i in range(0, len(pairs), 2):
        cells = []
        for key, cn in pairs[i:i + 2]:
            cells += [cn, snap.get(key) if snap.get(key) is not None else "—"]
        lines.append("| " + " | ".join(str(c) for c in cells) + " |")

    if report.financial_snapshot:
        lines += ["", "### 最新一期财务（Point-in-Time）", "",
                  "| 报告期 | 披露日 | 每股收益 | 营收同比% | 净利同比% | ROE% |",
                  "|---|---|---|---|---|---|"]
        fin = report.financial_snapshot
        lines.append(
            f"| {fin.get('report_date', '—')} | {fin.get('pub_date', '—')} "
            f"| {fin.get('eps', '—')} | {fin.get('revenue_yoy', '—')} "
            f"| {fin.get('net_profit_yoy', '—')} | {fin.get('roe', '—')} |"
        )

    lines += [
        "",
        "## 二、策略建议（Strategist）",
        "",
        "| 方向 | 仓位 | 介入 | 止损 | 止盈 | 持有周期 |",
        "|---|---|---|---|---|---|",
        f"| {LABEL_DIRECTION.get(advice.direction, advice.direction)} "
        f"| {advice.position_pct:.1f}% | {advice.entry if advice.entry else '—'} "
        f"| {advice.stop_loss if advice.stop_loss else '—'} "
        f"| {advice.take_profit if advice.take_profit else '—'} "
        f"| {advice.holding_period or '—'} |",
        "",
        f"- 推理依据：{advice.rationale or '—'}",
        f"- 失效条件：{advice.invalidation or '—'}",
        f"- 风险提示：{advice.risk_notes or '—'}",
        "",
        "## 三、反思校验（Critic）",
        "",
        f"**结论：{'通过' if verdict.passed else '不通过'}** "
        f"（阻断 {verdict.n_error} 项 / 提示 {verdict.n_warning} 项）",
        "",
        verdict.summary,
        "",
        f"已执行校验项：{', '.join(verdict.checked_fields)}",
        "",
    ]
    rule_issues = [i for i in verdict.issues if i.source == "rule"]
    llm_issues = [i for i in verdict.issues if i.source == "llm"]

    if rule_issues:
        lines += ["**规则层校验（确定性、可复现）**", "",
                  "| 编号 | 级别 | 问题 | 修复建议 |", "|---|---|---|---|"]
        for i in rule_issues:
            lines.append(f"| {i.code} | {i.severity} | {i.message} | {i.fix or '—'} |")
    else:
        lines.append("规则层未发现问题。")

    if llm_issues:
        lines += [
            "",
            "> **大模型语义校验（仅供参考，不作为判定依据）**  ",
            "> 语义层结论受模型能力限制，实测出现过模型把「做空止损应高于现价」"
            "判反的情况；是否被采纳由规则层与人工决定。",
            "",
            "| 编号 | 级别 | 问题 | 模型建议 |", "|---|---|---|---|",
        ]
        for i in llm_issues:
            lines.append(f"| {i.code} | {i.severity} | {i.message} | {i.fix or '—'} |")

    if verdict.revised is not None:
        r = verdict.revised
        lines += [
            "",
            "**自动修正后的建议**（仅修正确定性项，供人工复核参考）",
            "",
            f"- 方向 {LABEL_DIRECTION.get(r.direction, r.direction)}，"
            f"仓位 {r.position_pct:.1f}%，"
            f"止损 {r.stop_loss if r.stop_loss is not None else '—'}，"
            f"止盈 {r.take_profit if r.take_profit is not None else '—'}",
        ]

    lines += ["", "## 四、各阶段耗时", "", "| 阶段 | 耗时 |", "|---|---|"]
    for stage, ms in timings.items():
        if stage == "backend":
            continue
        lines.append(f"| {stage} | {ms:.0f} ms |")
    return "\n".join(lines)


def main() -> int:
    """主流程：解析参数 → 建后端 → 三 Agent 串联 → 输出报告。"""
    ap = argparse.ArgumentParser(description="三 Agent 工作流演示")
    ap.add_argument("--code", default="600519", help="股票代码（6 位）")
    ap.add_argument("--query", default=None, help="检索查询，默认自动生成")
    ap.add_argument("--as-of", dest="as_of", default=None,
                    help="数据截止日 YYYY-MM-DD（Point-in-Time 对齐）")
    ap.add_argument("--backend", choices=["mock", "qwen"], default="mock",
                    help="mock=离线桩（默认），qwen=真实推理")
    ap.add_argument("--top-k", type=int, default=8, help="检索证据条数")
    ap.add_argument("--no-llm-critic", action="store_true",
                    help="Critic 只跑规则层，不做大模型语义校验")
    ap.add_argument("--out", default=None, help="报告输出路径（默认 data/processed/）")
    args = ap.parse_args()

    setup_logging(level="INFO", log_file=str(ROOT / "logs" / "system.log"))

    print(f"=== 三 Agent 工作流 | 代码={args.code} 后端={args.backend} ===")
    llm = get_backend(args.backend)
    timings: dict = {"backend": args.backend}

    # 1. Analyst
    t0 = time.time()
    analyst = AnalystAgent(llm=llm, top_k=args.top_k)
    out_a = analyst.run({"stock_code": args.code, "query": args.query,
                         "as_of": args.as_of})
    timings["Analyst"] = (time.time() - t0) * 1000
    report = out_a["report"]
    print(f"[Analyst]   证据 {report.n_evidence} 条, 情感 {report.sentiment}, "
          f"LLM={report.llm_used} ({timings['Analyst']:.0f}ms)")

    # 2. Strategist
    t0 = time.time()
    strategist = StrategistAgent(llm=llm)
    out_s = strategist.run({"report": report})
    timings["Strategist"] = (time.time() - t0) * 1000
    advice = out_s["advice"]
    print(f"[Strategist] 方向 {advice.direction}, 仓位 {advice.position_pct:.1f}%, "
          f"LLM={advice.llm_used} ({timings['Strategist']:.0f}ms)")

    # 3. Critic
    t0 = time.time()
    critic = CriticAgent(llm=llm, use_llm=not args.no_llm_critic)
    out_c = critic.run({"report": report, "advice": advice})
    timings["Critic"] = (time.time() - t0) * 1000
    verdict = out_c["verdict"]
    print(f"[Critic]    通过={verdict.passed}, 阻断 {verdict.n_error} 项, "
          f"提示 {verdict.n_warning} 项 ({timings['Critic']:.0f}ms)")
    for i in verdict.issues:
        print(f"    - [{i.severity}] {i.code} {i.message}")

    # 4. 报告落盘
    md = build_markdown(report, advice, verdict, timings)
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
