# -*- coding: utf-8 -*-
"""stats.py — 批量评测指标统计

把一批 PipelineResult 压成论文可直接引用的指标：

    - 首轮通过率 / 终局通过率 / 闭环收敛增益    → 衡量迭代闭环的价值
    - 规则触发频次（首轮 vs 终局残留）          → 衡量每条校验规则的有效性
    - 终局来源分布 / 方向分布 / 情感分布        → 描述系统行为画像
    - 各阶段耗时                                → 性能基线

用法:
    from src.agents.stats import summarize_results
    stats = summarize_results(results)
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List

from src.agents.orchestrator import PipelineResult

__all__ = ["summarize_results"]


def _mean(values: List[float]) -> float:
    """均值（空列表返回 0.0），保留 2 位小数。"""
    return round(sum(values) / len(values), 2) if values else 0.0


def _rate(hit: int, total: int) -> float:
    """比率（0-1），保留 4 位小数；总数为 0 时返回 0.0。"""
    return round(hit / total, 4) if total else 0.0


def summarize_results(results: List[PipelineResult]) -> Dict[str, Any]:
    """汇总一批流水线结果。

    Args:
        results: `Orchestrator.run_batch` 的返回列表。

    Returns:
        统计字典，键说明见模块文档；数值已统一为 float 便于格式化输出。
    """
    n = len(results)
    if n == 0:
        return {"n_stocks": 0}

    n_first_pass = sum(1 for r in results if r.first_pass)
    n_final_pass = sum(1 for r in results if r.final_pass)
    n_retried = sum(1 for r in results if r.retried)

    # 规则触发频次：首轮（闭环介入前） vs 终局（闭环收敛后残留）
    first_hit: Counter = Counter()
    final_hit: Counter = Counter()
    for r in results:
        if r.rounds:
            first_hit.update(i.code for i in r.rounds[0].verdict.issues)
        final_hit.update(i.code for i in r.verdict.issues)

    timings_keys = ("Analyst", "Strategist", "Critic", "Total")
    timing_means = {
        f"avg_{k.lower()}_ms": _mean([float(r.timings.get(k, 0.0)) for r in results])
        for k in timings_keys
    }
    # 稳态耗时：剔除首只。批量场景下第一只要额外承担 bge-m3 / Qwen 的
    # 首次加载（实测约 14 秒），计入均值会把性能基线抬高一个量级。
    if n > 1:
        timing_means["avg_total_ms_steady"] = _mean(
            [float(r.timings.get("Total", 0.0)) for r in results[1:]]
        )

    return {
        "n_stocks": n,
        # 闭环价值：final - first 即「重试挽回的比例」
        "first_pass_rate": _rate(n_first_pass, n),
        "final_pass_rate": _rate(n_final_pass, n),
        "retry_gain": round(_rate(n_final_pass, n) - _rate(n_first_pass, n), 4),
        "n_first_pass": n_first_pass,
        "n_final_pass": n_final_pass,
        "n_retried": n_retried,
        "avg_rounds": _mean([float(r.n_rounds) for r in results]),
        "avg_evidence": _mean([float(r.report.n_evidence) for r in results]),
        "avg_position_pct": _mean([r.advice.position_pct for r in results]),
        "avg_confidence": _mean([r.advice.confidence for r in results]),
        "source_dist": dict(Counter(r.final_source for r in results)),
        "direction_dist": dict(Counter(r.advice.direction for r in results)),
        "sentiment_dist": dict(Counter(r.report.sentiment for r in results)),
        "rule_first_hit": dict(sorted(first_hit.items())),
        "rule_final_hit": dict(sorted(final_hit.items())),
        **timing_means,
    }
