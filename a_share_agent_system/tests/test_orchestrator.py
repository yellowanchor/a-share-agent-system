# -*- coding: utf-8 -*-
"""test_orchestrator.py — 编排层迭代闭环单元测试

测试策略：不加载任何模型。用「脚本化 Strategist」按轮次返回预设建议，
配合只跑规则层的 Critic，把闭环的分支确定性地走一遍：

    首轮通过 / 重试后通过 / 采纳自动修正 / 降级观望 / 重复问题提前终止

运行: python -m pytest tests/test_orchestrator.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agents.critic import CriticAgent  # noqa: E402
from src.agents.orchestrator import (  # noqa: E402
    Orchestrator,
    OrchestratorConfig,
    PipelineResult,
    RoundRecord,
)
from src.agents.schemas import (  # noqa: E402
    AnalystReport,
    CriticVerdict,
    Evidence,
    StrategyAdvice,
)
from src.agents.stats import summarize_results  # noqa: E402
from src.agents.strategist import StrategistAgent  # noqa: E402
from src.models.llm import MockLLMBackend  # noqa: E402


# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------
class StubAnalyst:
    """返回固定报告的 Analyst 替身（不触碰 DuckDB / bge-m3）。"""

    def __init__(self, report: AnalystReport) -> None:
        self._report = report
        self.calls: list = []

    def run(self, input_data: dict) -> dict:
        self.calls.append(dict(input_data))
        return {"report": self._report, "prompt": "", "elapsed_ms": 1.0}


class ScriptedStrategist(StrategistAgent):
    """按调用次序返回预设建议；用尽后重复最后一个。

    这样可以把「首轮坏 → 后续好」「一直坏」等场景精确编排出来，
    不必依赖模型输出（模型输出本身不可复现，不适合做闭环断言）。
    """

    def __init__(self, advices: list) -> None:
        super().__init__(llm=None)
        self._advices = list(advices)
        self.calls: list = []

    def run(self, input_data: dict) -> dict:
        self.calls.append(dict(input_data))
        i = min(len(self.calls) - 1, len(self._advices) - 1)
        return {"advice": self._advices[i], "prompt": "",
                "reference": {}, "elapsed_ms": 1.0}


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------
def _report(sentiment: str = "positive", close: float = 100.0) -> AnalystReport:
    """含 1 条证据、现价 100 元的报告。"""
    return AnalystReport(
        stock_code="600519",
        as_of="2026-08-20",
        sentiment=sentiment,
        confidence=0.7,
        evidence=[Evidence(idx=1, title="业绩预增", source="测试",
                           publish_time="2026-08-15", sentiment=sentiment)],
        market_snapshot={"close": close, "atr14": 5.0,
                         "support": 90.0, "resistance": 120.0},
    )


def _good_advice() -> StrategyAdvice:
    """规则层全过的建议：做多、仓位 10%、止损 90、止盈 115。"""
    return StrategyAdvice(
        direction="long", position_pct=10.0, entry=100.0,
        stop_loss=90.0, take_profit=115.0,
        holding_period="5-10个交易日", invalidation="跌破止损位",
        rationale="业绩预增，趋势向上[证据 1]", evidence_ids=[1],
        risk_notes="注意大盘风险", confidence=0.6,
    )


def _fixable_advice() -> StrategyAdvice:
    """可被 Critic 自动修正的建议：仓位超限 + 止损止盈方向反了。"""
    return StrategyAdvice(
        direction="long", position_pct=50.0, entry=100.0,
        stop_loss=120.0, take_profit=90.0,
        holding_period="5-10个交易日", invalidation="跌破止损位",
        rationale="业绩预增，趋势向上[证据 1]", evidence_ids=[1],
        risk_notes="注意大盘风险", confidence=0.6,
    )


def _unfixable_advice() -> StrategyAdvice:
    """无法自动修正的建议：仓位超限 + 缺止损（修正后仍不通过）。"""
    return StrategyAdvice(
        direction="long", position_pct=50.0, entry=100.0,
        stop_loss=None, take_profit=115.0,
        holding_period="5-10个交易日", invalidation="跌破止损位",
        rationale="业绩预增，趋势向上[证据 1]", evidence_ids=[1],
        risk_notes="注意大盘风险", confidence=0.6,
    )


def _orch(advices: list, report: AnalystReport, **cfg) -> Orchestrator:
    """构造注入好替身的编排器。"""
    return Orchestrator(
        config=OrchestratorConfig(**cfg) if cfg else None,
        analyst=StubAnalyst(report),
        strategist=ScriptedStrategist(advices),
        critic=CriticAgent(llm=None, use_llm=False),
    )


# ---------------------------------------------------------------------------
# 闭环分支
# ---------------------------------------------------------------------------
def test_first_pass_stops_immediately():
    """首轮即通过：只跑 1 轮，不做任何重试。"""
    orch = _orch([_good_advice()], _report())
    res = orch.run({"stock_code": "600519"})["result"]

    assert res.n_rounds == 1
    assert res.first_pass is True
    assert res.final_pass is True
    assert res.final_source == "first_pass"
    assert res.stop_reason == "passed"
    assert res.retried is False
    # Analyst 只被调用一次（证据客观，重跑无意义）
    assert len(orch.analyst.calls) == 1


def test_retry_then_pass():
    """首轮失败、第二轮成功：闭环真正产生价值的情形。"""
    orch = _orch([_fixable_advice(), _good_advice()], _report(), max_retries=2)
    res = orch.run({"stock_code": "600519"})["result"]

    assert res.n_rounds == 2
    assert res.first_pass is False
    assert res.final_pass is True
    assert res.final_source == "retry_pass"
    assert res.retried is True
    # 第二轮必须带上第一轮的反馈
    second_call = orch.strategist.calls[1]
    assert "P001" in second_call["feedback"]
    # 温度按衰减系数下调（收敛）
    assert second_call["temperature"] < orch.strategist.calls[0]["temperature"]


def test_revised_when_retries_exhausted():
    """重试用尽且 Critic 能自动修正：采纳修正版并复检通过。"""
    # 一直返回「可修正但修正前不通过」的建议 → 修正版应被采纳
    orch = _orch([_fixable_advice()], _report(), max_retries=1)
    res = orch.run({"stock_code": "600519"})["result"]

    assert res.n_rounds == 2          # 首轮 + 1 次重试
    assert res.final_source == "revised"
    assert res.final_pass is True     # 修正版复检通过
    assert res.advice.position_pct <= 20.0      # 仓位已收敛到上限
    assert res.advice.stop_loss < res.advice.take_profit   # 止损止盈已互换


def test_fallback_when_unfixable():
    """无法修正时降级为观望 + 零仓位，且不给出任何可执行方向。"""
    orch = _orch([_unfixable_advice()], _report(), max_retries=1)
    res = orch.run({"stock_code": "600519"})["result"]

    assert res.final_source == "fallback"
    assert res.advice.direction == "watch"
    assert res.advice.position_pct == 0.0
    assert res.advice.stop_loss is None and res.advice.take_profit is None
    assert "降级" in res.advice.risk_notes
    # 降级后必须能通过规则校验，否则等于把坏建议放出去
    assert res.final_pass is True


def test_stop_on_repeat():
    """连续两轮问题集合完全相同 → 提前终止，不再空耗重试次数。"""
    orch = _orch([_fixable_advice()], _report(), max_retries=3)
    res = orch.run({"stock_code": "600519"})["result"]

    assert res.stop_reason == "repeated"
    assert res.n_rounds == 2          # 第 2 轮问题与上轮一致即停（未用满 3 次）


def test_no_retry_when_max_retries_zero():
    """max_retries=0 等价于关闭闭环（对照实验用）。"""
    orch = _orch([_fixable_advice(), _good_advice()], _report(), max_retries=0)
    res = orch.run({"stock_code": "600519"})["result"]

    assert res.n_rounds == 1
    assert res.first_pass is False


def test_feedback_text_lists_error_codes():
    """反馈文本必须列出问题编号与修复建议，且顺序稳定。"""
    from src.agents.schemas import CriticIssue

    verdict = CriticVerdict(issues=[
        CriticIssue(code="P001", severity="error", message="仓位超限", fix="降到20"),
        CriticIssue(code="T002", severity="warning", message="缺持有周期", fix="补充"),
    ])
    text = Orchestrator._feedback_text(verdict)

    assert "P001" in text and "仓位超限" in text and "降到20" in text
    assert "T002" in text
    # error 排在 warning 之前
    assert text.index("P001") < text.index("T002")


# ---------------------------------------------------------------------------
# 批量执行
# ---------------------------------------------------------------------------
def test_run_batch_reuses_agents_and_continues_on_error():
    """批量复用同一批 Agent；单只异常不中断整批。"""
    orch = _orch([_good_advice()], _report())
    progress: list = []
    results = orch.run_batch(
        ["600519", "600030", "600276"],
        on_progress=lambda i, n, r: progress.append((i, n, r.stock_code)),
    )

    assert len(results) == 3
    assert [r.stock_code for r in results] == ["600519", "600030", "600276"]
    # Agent 实例复用：Analyst 被调用 3 次，但始终是同一个对象
    assert len(orch.analyst.calls) == 3
    assert len(progress) == 3


def test_batch_failure_produces_placeholder():
    """流水线抛异常时产出可序列化的占位结果，而不是让整批崩掉。"""

    class BoomAnalyst:
        def run(self, input_data: dict) -> dict:
            raise RuntimeError("数据源不可用")

    orch = Orchestrator(config=OrchestratorConfig(max_retries=0),
                        analyst=BoomAnalyst(),
                        strategist=ScriptedStrategist([_good_advice()]),
                        critic=CriticAgent(llm=None, use_llm=False))
    results = orch.run_batch(["600519"])

    assert len(results) == 1
    assert results[0].final_pass is False
    assert results[0].final_source == "fallback"
    assert results[0].verdict.issues[0].code == "S001"
    # 占位结果必须能序列化（批量报告要落盘 JSON）
    assert results[0].to_json_dict()["stock_code"] == "600519"


# ---------------------------------------------------------------------------
# Critic 公开复检接口
# ---------------------------------------------------------------------------
def test_critic_check_rules_without_llm():
    """check_rules 只跑规则层，不调用模型，可反复使用。"""
    critic = CriticAgent(llm=None, use_llm=False)
    report, advice = _report(), _good_advice()

    v1 = critic.check_rules(report, advice)
    assert v1.passed is True
    assert v1.llm_used is False

    v2 = critic.check_rules(report, _fixable_advice())
    assert v2.passed is False
    assert "P001" in [i.code for i in v2.issues]


# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------
def _mk_result(code: str, first: bool, final: bool, n_rounds: int,
               source: str, direction: str = "long") -> PipelineResult:
    """构造一个用于统计断言的结果对象。"""
    verdict = CriticVerdict(passed=final)
    first_verdict = CriticVerdict(passed=first)
    return PipelineResult(
        stock_code=code, report=_report(), advice=_good_advice(),
        verdict=verdict,
        rounds=[RoundRecord(round=1, advice=_good_advice(), verdict=first_verdict)],
        n_rounds=n_rounds, first_pass=first, final_pass=final,
        final_source=source, stop_reason="passed",
        timings={"Analyst": 100.0, "Strategist": 20.0, "Critic": 10.0, "Total": 130.0},
    )


def test_summarize_results_metrics():
    """首轮 1/2 通过、终局 2/2 通过 → 闭环增益 0.5。"""
    results = [
        _mk_result("A", first=True, final=True, n_rounds=1, source="first_pass"),
        _mk_result("B", first=False, final=True, n_rounds=2, source="retry_pass"),
    ]
    s = summarize_results(results)

    assert s["n_stocks"] == 2
    assert s["first_pass_rate"] == 0.5
    assert s["final_pass_rate"] == 1.0
    assert s["retry_gain"] == 0.5
    assert s["n_retried"] == 1
    assert s["avg_rounds"] == 1.5
    assert s["avg_total_ms"] == 130.0
    assert s["source_dist"] == {"first_pass": 1, "retry_pass": 1}
    assert s["direction_dist"] == {"long": 2}


def test_summarize_results_empty():
    """空列表不应抛异常。"""
    assert summarize_results([])["n_stocks"] == 0


# ---------------------------------------------------------------------------
# 反馈注入提示词
# ---------------------------------------------------------------------------
def test_feedback_injected_into_strategist_prompt():
    """反馈必须进入提示词，并明确要求「其余字段沿用」。"""
    st = StrategistAgent(llm=MockLLMBackend())
    out = st.run({"report": _report(), "feedback": "- 【必须修正】P001: 仓位超限"})

    assert "上一轮校验未通过" in out["prompt"]
    assert "P001" in out["prompt"]
    assert "沿用上一轮" in out["prompt"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
