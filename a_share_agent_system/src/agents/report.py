# -*- coding: utf-8 -*-
"""report.py — 三 Agent 结果的 Markdown 渲染

为什么单独放一个模块
--------------------
单只演示（scripts/run_agent_demo.py）与批量评测（scripts/batch_eval.py）
要输出同样格式的报告。若各自实现一遍渲染，两处版式必然漂移，
论文里的截图与附录就会对不上。这里作为**单一渲染源**被两处共用。

用法:
    from src.agents.report import build_markdown, build_batch_markdown

    md = build_markdown(report, advice, verdict, timings, result=result)
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from src.agents.schemas import AnalystReport, CriticVerdict, StrategyAdvice

__all__ = [
    "LABEL_SENTIMENT", "LABEL_DIRECTION", "LABEL_IMPACT", "LABEL_SOURCE",
    "build_markdown", "build_batch_markdown",
]

LABEL_SENTIMENT = {"positive": "利好", "negative": "利空", "neutral": "中性"}
LABEL_DIRECTION = {"long": "做多", "short": "做空", "watch": "观望"}
LABEL_IMPACT = {"major": "重大", "moderate": "一般", "minor": "轻微"}

#: 编排层终局类型的中文标签（报告中标注闭环收敛路径）
LABEL_SOURCE = {
    "first_pass": "首轮通过",
    "retry_pass": "重试后通过",
    "revised": "采纳自动修正",
    "fallback": "降级为观望",
}

#: 量价快照展示顺序与中文标签
_SNAPSHOT_PAIRS = (
    ("close", "最新收盘"), ("ma20", "MA20"), ("ma60", "MA60"),
    ("rsi14", "RSI14"), ("atr14", "ATR14"), ("ret_20d", "近20日涨跌%"),
    ("vol_ratio", "量比"), ("annual_vol", "年化波动率"),
    ("support", "支撑"), ("resistance", "阻力"),
)


def build_markdown(
    report: AnalystReport,
    advice: StrategyAdvice,
    verdict: CriticVerdict,
    timings: Dict[str, Any],
    result: Optional[Any] = None,
) -> str:
    """把三 Agent 结果渲染为 Markdown 报告。

    Args:
        report: Analyst 研判报告。
        advice: 最终策略建议。
        verdict: 与最终建议对应的校验结论。
        timings: 各阶段耗时（毫秒），键为阶段名；键 "backend" 会单独处理。
        result: PipelineResult，提供时额外输出「迭代收敛过程」章节。

    Returns:
        Markdown 文本。
    """
    lines: List[str] = [
        f"# {report.stock_code} 舆情与策略分析报告（三 Agent 协作）",
        "",
        f"- 数据截止日：{report.as_of or '未指定'}",
        f"- 检索查询：{report.query}",
        f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- LLM 后端：{timings.get('backend', '—')}"
        f"（Analyst={report.llm_used} / Strategist={advice.llm_used} / "
        f"Critic语义校验={verdict.llm_used}）",
    ]

    # --- 闭环收敛信息（仅编排层结果才有）---
    if result is not None:
        lines += [
            f"- 迭代轮次：{result.n_rounds}"
            f"（首轮{'通过' if result.first_pass else '未通过'} → "
            f"终局{('通过' if result.final_pass else '未通过')}）",
            f"- 终局来源：{LABEL_SOURCE.get(result.final_source, result.final_source)}"
            f"（终止原因：{result.stop_reason}）",
        ]
    lines.append("")

    # --- 一、舆情研判 ---
    lines += [
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

    lines += ["", "### 量价快照", "", "| 指标 | 数值 | 指标 | 数值 |", "|---|---|---|---|"]
    snap = report.market_snapshot or {}
    for i in range(0, len(_SNAPSHOT_PAIRS), 2):
        cells: List[Any] = []
        for key, cn in _SNAPSHOT_PAIRS[i:i + 2]:
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

    # --- 二、策略建议 ---
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
    ]

    # --- 三、迭代收敛过程（编排层独有）---
    if result is not None and result.rounds:
        lines += [
            "## 三、迭代收敛过程（Orchestrator）",
            "",
            "| 轮次 | 方向 | 仓位 | 温度 | 通过 | 阻断问题 | 耗时 |",
            "|---|---|---|---|---|---|---|",
        ]
        for r in result.rounds:
            errs = "、".join(r.error_codes) or "—"
            lines.append(
                f"| {r.round} "
                f"| {LABEL_DIRECTION.get(r.advice.direction, r.advice.direction)} "
                f"| {r.advice.position_pct:.1f}% | {r.temperature} "
                f"| {'✅' if r.verdict.passed else '❌'} | {errs} "
                f"| {r.elapsed_ms:.0f}ms |"
            )
        if result.retried:
            lines += [
                "",
                f"首轮阻断问题：{'、'.join(result.first_round_error_codes) or '无'}"
                f"；经 {result.n_rounds - 1} 次反馈重试后"
                f"{'收敛' if result.final_pass else '仍未收敛'}。",
            ]
        lines.append("")

    # --- 四、反思校验 ---
    idx = "四" if (result is not None and result.rounds) else "三"
    lines += [
        f"## {idx}、反思校验（Critic）",
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

    # --- 末章：耗时 ---
    lines += ["", f"## {'五' if (result is not None and result.rounds) else '四'}、各阶段耗时",
              "", "| 阶段 | 耗时 |", "|---|---|"]
    for stage, ms in timings.items():
        if stage == "backend":
            continue
        lines.append(f"| {stage} | {ms:.0f} ms |")
    return "\n".join(lines)


def build_batch_markdown(
    results: List[Any],
    meta: Dict[str, Any],
    stats: Dict[str, Any],
) -> str:
    """批量评测报告。

    Args:
        results: PipelineResult 列表。
        meta: 运行元信息（backend / as_of / max_retries / 生成时间等）。
        stats: `summarize_results` 产出的统计字典。

    Returns:
        Markdown 文本。
    """
    lines: List[str] = [
        "# 多股票批量评测报告（三 Agent 编排层）",
        "",
        f"- 生成时间：{meta.get('generated_at', '—')}",
        f"- LLM 后端：{meta.get('backend', '—')}",
        f"- 数据截止日：{meta.get('as_of') or '最新'}",
        f"- 股票数量：{len(results)}　最大重试次数：{meta.get('max_retries', '—')}"
        f"　生成侧仓位上限：{meta.get('max_position_pct', 20)}%"
        f"　校验侧仓位上限：{meta.get('risk_position_pct', 20)}%",
        f"- 证据条数上限（top_k）：{meta.get('top_k', '—')}",
        "",
        "## 一、总体指标",
        "",
        "| 指标 | 数值 |",
        "|---|---|",
    ]
    metric_labels = (
        ("n_stocks", "评测股票数"),
        ("first_pass_rate", "首轮通过率"),
        ("final_pass_rate", "终局通过率"),
        ("retry_gain", "闭环收敛增益"),
        ("n_retried", "发生重试的股票数"),
        ("avg_rounds", "平均迭代轮次"),
        ("avg_evidence", "平均证据条数"),
        ("avg_analyst_ms", "Analyst 平均耗时(ms)"),
        ("avg_strategist_ms", "Strategist 平均耗时(ms)"),
        ("avg_critic_ms", "Critic 平均耗时(ms)"),
        ("avg_total_ms", "单只平均总耗时(ms)"),
        ("avg_total_ms_steady", "稳态耗时(ms，剔除首只)"),
    )
    for key, cn in metric_labels:
        if key in stats:
            v = stats[key]
            lines.append(f"| {cn} | {v:.2f} |" if isinstance(v, float) else f"| {cn} | {v} |")
    lines += [
        "",
        "> 注：首只要额外承担 bge-m3 与 Qwen 的首次加载（实测约 14 秒）；"
        "「稳态耗时」剔除了这只，用于刻画批量推理的性能基线。",
    ]

    lines += ["", "### 终局来源分布", "", "| 终局类型 | 数量 | 占比 |", "|---|---|---|"]
    n = max(1, len(results))
    for src, cnt in stats.get("source_dist", {}).items():
        lines.append(f"| {LABEL_SOURCE.get(src, src)} | {cnt} | {cnt / n:.0%} |")

    lines += ["", "### 策略方向分布", "", "| 方向 | 数量 | 占比 |", "|---|---|---|"]
    for d, cnt in stats.get("direction_dist", {}).items():
        lines.append(f"| {LABEL_DIRECTION.get(d, d)} | {cnt} | {cnt / n:.0%} |")

    lines += ["", "### 舆情情感分布", "", "| 情感 | 数量 | 占比 |", "|---|---|---|"]
    for s, cnt in stats.get("sentiment_dist", {}).items():
        lines.append(f"| {LABEL_SENTIMENT.get(s, s)} | {cnt} | {cnt / n:.0%} |")

    # --- 规则触发频次（首轮 vs 终局）---
    lines += [
        "",
        "## 二、校验规则触发频次",
        "",
        "首轮为「闭环介入前」的问题分布，终局为「闭环收敛后」的残留问题。"
        "两者之差即可衡量迭代闭环实际修掉了哪些错误。E/P/C/N/T 系为规则层"
        "确定性检出；L 系为大模型语义层检出的提示项（仅供参考）。",
        "",
        "| 规则编号 | 首轮触发 | 终局残留 | 变化 |",
        "|---|---|---|---|",
    ]
    first_hit = stats.get("rule_first_hit", {})
    final_hit = stats.get("rule_final_hit", {})
    for code in sorted(set(first_hit) | set(final_hit)):
        f = first_hit.get(code, 0)
        l = final_hit.get(code, 0)
        delta = f - l
        if delta > 0:
            cell = f"修复 {delta}"
        elif delta < 0:
            # 终局新出现的问题往往源于重试轮改变了其他字段
            #（例如修正仓位时把方向也改了）——这正是闭环的副作用，值得单独观察
            cell = f"**新增 {-delta}**"
        else:
            cell = "—"
        lines.append(f"| {code} | {f} | {l} | {cell} |")
    if not first_hit and not final_hit:
        lines.append("| — | 0 | 0 | — |")

    # --- 逐只明细 ---
    lines += [
        "",
        "## 三、逐只明细",
        "",
        "| 代码 | 情感 | 证据 | 首轮 | 终局 | 轮次 | 终局来源 | 方向 | 仓位 | 阻断问题 | 总耗时 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        rep, adv = r.report, r.advice
        errs = "、".join(i.code for i in r.verdict.issues
                        if i.severity == "error") or "—"
        lines.append(
            f"| {r.stock_code} "
            f"| {LABEL_SENTIMENT.get(rep.sentiment, rep.sentiment)} "
            f"| {rep.n_evidence} "
            f"| {'✅' if r.first_pass else '❌'} "
            f"| {'✅' if r.final_pass else '❌'} "
            f"| {r.n_rounds} "
            f"| {LABEL_SOURCE.get(r.final_source, r.final_source)} "
            f"| {LABEL_DIRECTION.get(adv.direction, adv.direction)} "
            f"| {adv.position_pct:.1f}% "
            f"| {errs} "
            f"| {r.timings.get('Total', 0):.0f}ms |"
        )

    lines += ["", "---", "",
              "> 本报告由 `scripts/batch_eval.py` 自动生成，"
              "结构化结果同步落盘为同名 `.json`，可复用于论文图表与前端展示。"]
    return "\n".join(lines)
