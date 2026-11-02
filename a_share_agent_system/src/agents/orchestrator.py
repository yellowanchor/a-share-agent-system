# -*- coding: utf-8 -*-
"""orchestrator.py — 三 Agent 工作流编排层（含迭代闭环）

为什么需要单独一层
------------------
Phase 3.1 把三个 Agent 串成了**单向流水线**：Analyst → Strategist → Critic，
Critic 检出的错误只写进报告，没有回头修正的机会。这在工程上等于「发现问题
但不解决问题」，Critic 的产出只用于提示，闭环是断开的。

本模块补上这一段：

    Analyst（只跑一次）→ ┌→ Strategist → Critic ─┐
                        └──── 不通过则带反馈重试 ←──┘  （最多 max_retries 次）

三条设计原则（对应论文第 4 章「反思式迭代」）
--------------------------------------------
1. **Analyst 不重试**：证据链与量价快照是客观数据，重跑不改变结果，
   却要再付一次检索与 40 秒以上的推理开销。只重跑「生成侧」。
2. **反馈要限定修改范围**：只说「你错了请改」会让模型另起炉灶，
   连原本正确的方向一起改掉。提示词里显式要求「除上述问题外沿用上一轮」。
3. **重试不是无限兜底**：连续两轮报出完全相同的问题集合，说明反馈没带来
   新信息，立即终止（避免烧算力换一个必然失败的结果）。

收敛后的三种终局
----------------
    first_pass   首轮即通过
    retry_pass   重试后通过（闭环真正产生价值的情形）
    revised      达到重试上限，采纳 Critic 的确定性自动修正版
    fallback     无法修正，降级为「观望 + 0 仓位」的保守建议

用法:
    from src.agents import Orchestrator
    from src.models.llm import get_backend

    orch = Orchestrator(llm=get_backend("mock"))
    out = orch.run({"stock_code": "600519", "as_of": "2026-08-20"})
    result = out["result"]          # PipelineResult
    print(result.n_rounds, result.final_source, result.verdict.passed)

    # 批量（复用同一批 Agent，避免反复加载 bge-m3 / Qwen）
    results = orch.run_batch(["600519", "600030"], as_of="2026-08-20")
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, Field

from src.agents.schemas import AnalystReport, CriticVerdict, StrategyAdvice

logger = logging.getLogger(__name__)

__all__ = ["OrchestratorConfig", "RoundRecord", "PipelineResult", "Orchestrator"]

#: 终局类型枚举（用于报告与统计）
FINAL_SOURCES = ("first_pass", "retry_pass", "revised", "fallback")


class OrchestratorConfig(BaseModel):
    """编排层配置。

    Attributes:
        max_retries: Critic 不通过时的最大重试次数（不含首轮）。
        retry_on: 触发重试的级别，"error"（默认，仅阻断项）或 "any"（含提示项）。
        use_revised: 达到重试上限时，是否采纳 Critic 的确定性自动修正版。
        temperature_decay: 每重试一轮的策略温度衰减系数（收敛用）。
        stop_on_repeat: 连续两轮问题集合完全相同则提前终止。
    """

    max_retries: int = Field(default=2, ge=0, le=5)
    retry_on: str = "error"
    use_revised: bool = True
    temperature_decay: float = Field(default=0.7, gt=0.0, le=1.0)
    stop_on_repeat: bool = True


class RoundRecord(BaseModel):
    """一轮「生成 → 校验」的记录。

    Attributes:
        round: 轮次序号（从 1 开始）。
        advice: 本轮策略建议。
        verdict: 本轮 Critic 结论。
        feedback: 注入本轮的反馈文本（首轮为空）。
        temperature: 本轮使用的温度。
        strategist_ms / critic_ms: 各阶段耗时。
        elapsed_ms: 本轮总耗时。
    """

    round: int = Field(ge=1)
    advice: StrategyAdvice
    verdict: CriticVerdict
    feedback: str = ""
    temperature: float = 0.0
    strategist_ms: float = 0.0
    critic_ms: float = 0.0
    elapsed_ms: float = 0.0

    @property
    def error_codes(self) -> List[str]:
        """本轮 error 级问题编号。"""
        return [i.code for i in self.verdict.issues if i.severity == "error"]

    @property
    def all_codes(self) -> List[str]:
        """本轮全部问题编号。"""
        return [i.code for i in self.verdict.issues]


class PipelineResult(BaseModel):
    """单只股票的完整流水线结果。

    Attributes:
        stock_code: 股票代码。
        as_of: 数据截止日。
        report: Analyst 研判报告（含证据链）。
        advice: 最终采纳的策略建议。
        verdict: 与最终建议对应的校验结论。
        rounds: 逐轮记录（首轮 + 每次重试）。
        n_rounds: 实际执行轮数。
        first_pass: 首轮是否通过。
        final_pass: 终局是否通过。
        final_source: 终局类型（first_pass / retry_pass / revised / fallback）。
        stop_reason: 终止原因（passed / max_retries / repeated / no_issues）。
        timings: 各阶段耗时（毫秒）。
    """

    stock_code: str = ""
    as_of: str = ""
    report: AnalystReport
    advice: StrategyAdvice
    verdict: CriticVerdict
    rounds: List[RoundRecord] = Field(default_factory=list)
    n_rounds: int = 1
    first_pass: bool = False
    final_pass: bool = False
    final_source: str = "fallback"
    stop_reason: str = ""
    timings: Dict[str, float] = Field(default_factory=dict)

    @property
    def retried(self) -> bool:
        """是否发生过重试。"""
        return self.n_rounds > 1

    @property
    def first_round_error_codes(self) -> List[str]:
        """首轮阻断问题编号（用于统计「闭环修掉了什么」）。"""
        return self.rounds[0].error_codes if self.rounds else []

    def to_json_dict(self) -> Dict[str, Any]:
        """转为可 JSON 序列化的字典（供报告与前端消费）。"""
        return self.model_dump(mode="json")


class Orchestrator:
    """三 Agent 工作流编排器。

    持有三个 Agent 实例并复用它们：bge-m3 与 Qwen 的加载开销很大
    （首次约 40 秒），批量评测时若每只股票重建 Agent，绝大部分时间会耗在
    重复加载上。

    Attributes:
        config: 编排配置。
        analyst / strategist / critic: 三个 Agent 实例（懒加载）。
    """

    def __init__(
        self,
        llm: Optional[Any] = None,
        config: Optional[OrchestratorConfig] = None,
        analyst: Optional[Any] = None,
        strategist: Optional[Any] = None,
        critic: Optional[Any] = None,
        **agent_kwargs: Any,
    ) -> None:
        """初始化。

        Args:
            llm: LLM 后端（None 时各 Agent 自行取默认后端）。
            config: 编排配置（None 时取 settings.yaml 的 orchestrator 段）。
            analyst / strategist / critic: 已构造好的 Agent（测试注入用）。
            **agent_kwargs: 透传给自动构造的 Agent（如 top_k）。
        """
        from src.config import get

        if config is None:
            config = OrchestratorConfig(
                max_retries=int(get("orchestrator.max_retries", 2)),
                retry_on=str(get("orchestrator.retry_on", "error")),
                use_revised=bool(get("orchestrator.use_revised", True)),
                temperature_decay=float(get("orchestrator.temperature_decay", 0.7)),
                stop_on_repeat=bool(get("orchestrator.stop_on_repeat", True)),
            )
        self.config = config
        self.llm = llm
        self._agent_kwargs = agent_kwargs
        self._analyst = analyst
        self._strategist = strategist
        self._critic = critic

    # ------------------------------------------------------------------
    # Agent 实例（懒加载 + 复用）
    # ------------------------------------------------------------------
    @property
    def analyst(self):
        """Analyst 实例（懒加载）。"""
        if self._analyst is None:
            from src.agents.analyst import AnalystAgent
            self._analyst = AnalystAgent(llm=self.llm, **self._agent_kwargs)
        return self._analyst

    @property
    def strategist(self):
        """Strategist 实例（懒加载）。"""
        if self._strategist is None:
            from src.agents.strategist import StrategistAgent
            self._strategist = StrategistAgent(llm=self.llm)
        return self._strategist

    @property
    def critic(self):
        """Critic 实例（懒加载）。"""
        if self._critic is None:
            from src.agents.critic import CriticAgent
            self._critic = CriticAgent(llm=self.llm)
        return self._critic

    # ------------------------------------------------------------------
    # 单只股票主流程
    # ------------------------------------------------------------------
    def run(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """执行完整流水线（含迭代闭环）。

        Args:
            input_data: 需含 `stock_code`；可选 `as_of` / `query` / `top_k`。

        Returns:
            {"result": PipelineResult, "report_out": dict, "elapsed_ms": float}
        """
        t0 = time.time()
        code = str(input_data.get("stock_code", "")).strip()
        if not code:
            raise ValueError("input_data 必须提供 stock_code")

        # --- 1. Analyst：只跑一次（证据与行情客观，重跑无意义）---
        t = time.time()
        payload: Dict[str, Any] = {
            "stock_code": code,
            "query": input_data.get("query"),
            "as_of": input_data.get("as_of"),
        }
        if input_data.get("top_k"):
            payload["top_k"] = int(input_data["top_k"])
        out_a = self.analyst.run(payload)
        report = out_a["report"]
        analyst_ms = (time.time() - t) * 1000

        # --- 2. 生成—校验迭代闭环 ---
        rounds: List[RoundRecord] = []
        feedback = ""
        temperature = float(self.strategist.config.temperature)
        advice: Optional[StrategyAdvice] = None
        verdict: Optional[CriticVerdict] = None
        final_source = "fallback"
        stop_reason = ""
        prev_codes: Optional[Set[str]] = None

        max_attempts = self.config.max_retries + 1
        for attempt in range(1, max_attempts + 1):
            t = time.time()
            out_s = self.strategist.run({
                "report": report,
                "feedback": feedback,
                "temperature": temperature,
            })
            advice = out_s["advice"]
            s_ms = (time.time() - t) * 1000

            t = time.time()
            out_c = self.critic.run({"report": report, "advice": advice})
            verdict = out_c["verdict"]
            c_ms = (time.time() - t) * 1000

            rounds.append(RoundRecord(
                round=attempt, advice=advice, verdict=verdict,
                feedback=feedback, temperature=temperature,
                strategist_ms=s_ms, critic_ms=c_ms, elapsed_ms=s_ms + c_ms,
            ))
            logger.info(
                "[Orchestrator] %s 第%d轮 通过=%s 阻断=%s",
                code, attempt, verdict.passed,
                "、".join(rounds[-1].error_codes) or "无",
            )

            if verdict.passed:
                final_source = "first_pass" if attempt == 1 else "retry_pass"
                stop_reason = "passed"
                break

            # 未通过：判断是否值得再试
            codes = set(rounds[-1].all_codes if self.config.retry_on == "any"
                        else rounds[-1].error_codes)
            if attempt == max_attempts:
                stop_reason = "max_retries"
                break
            if self.config.stop_on_repeat and codes and codes == prev_codes:
                # 反馈没有带来任何新信息，再试大概率同样失败
                stop_reason = "repeated"
                break
            if not codes:
                stop_reason = "no_issues"
                break

            prev_codes = codes
            feedback = self._feedback_text(verdict)
            temperature = round(temperature * self.config.temperature_decay, 4)

        # --- 3. 终局处理：修正 / 降级 ---
        assert advice is not None and verdict is not None
        if stop_reason != "passed":
            advice, verdict, final_source = self._finalize(report, advice, verdict)

        total_ms = (time.time() - t0) * 1000
        result = PipelineResult(
            stock_code=code,
            as_of=report.as_of,
            report=report,
            advice=advice,
            verdict=verdict,
            rounds=rounds,
            n_rounds=len(rounds),
            first_pass=rounds[0].verdict.passed if rounds else False,
            final_pass=verdict.passed,
            final_source=final_source,
            stop_reason=stop_reason,
            timings={
                "Analyst": analyst_ms,
                "Strategist": sum(r.strategist_ms for r in rounds),
                "Critic": sum(r.critic_ms for r in rounds),
                "Total": total_ms,
            },
        )
        logger.info(
            "[Orchestrator] %s 完成 轮次=%d 首轮通过=%s 终局=%s（%s）%.0fms",
            code, result.n_rounds, result.first_pass,
            result.final_pass, final_source, total_ms,
        )
        return {"result": result, "report_out": out_a, "elapsed_ms": total_ms}

    # ------------------------------------------------------------------
    # 批量执行
    # ------------------------------------------------------------------
    def run_batch(
        self,
        codes: List[str],
        as_of: Optional[str] = None,
        query: Optional[str] = None,
        top_k: Optional[int] = None,
        on_progress: Optional[Any] = None,
    ) -> List[PipelineResult]:
        """批量执行（复用同一批 Agent）。

        Args:
            codes: 股票代码列表。
            as_of: 统一数据截止日。
            query: 统一检索词（None 时由 Analyst 自动生成）。
            top_k: 证据条数。
            on_progress: 每只完成后的回调，签名 `(i, n, result)`。

        Returns:
            PipelineResult 列表（失败的股票以 fallback 结果占位，不中断整批）。
        """
        results: List[PipelineResult] = []
        n = len(codes)
        for i, code in enumerate(codes, 1):
            try:
                out = self.run({"stock_code": code, "as_of": as_of,
                                "query": query, "top_k": top_k})
                res = out["result"]
            except Exception as exc:
                logger.error("批量执行失败 %s: %s", code, exc)
                res = self._empty_result(str(code), str(exc))
            results.append(res)
            if on_progress:
                on_progress(i, n, res)
        return results

    # ------------------------------------------------------------------
    # 反馈与终局
    # ------------------------------------------------------------------
    @staticmethod
    def _feedback_text(verdict: CriticVerdict) -> str:
        """把 Critic 的问题列表渲染成注入下一轮的反馈文本。

        只挑阻断与提示项，按编号排序，保证同一组问题得到稳定的反馈文本
        （可复现性对论文实验很重要）。
        """
        items = [i for i in verdict.issues if i.severity in ("error", "warning")]
        if not items:
            return ""
        lines: List[str] = []
        for i in sorted(items, key=lambda x: (x.severity != "error", x.code)):
            tag = "【必须修正】" if i.severity == "error" else "【建议修正】"
            line = f"- {tag} {i.code}: {i.message}"
            if i.fix:
                line += f"（{i.fix}）"
            lines.append(line)
        return "\n".join(lines)

    def _finalize(
        self,
        report: AnalystReport,
        advice: StrategyAdvice,
        verdict: CriticVerdict,
    ):
        """达到重试上限后的终局处理：优先采纳自动修正，否则保守降级。

        Returns:
            (最终建议, 复检结论, 终局类型)
        """
        # 1. Critic 给出的确定性修正版（仓位收敛 / 止损止盈互换 / 剔除越界证据）
        if self.config.use_revised and verdict.revised is not None:
            recheck = self.critic.check_rules(report, verdict.revised)
            if recheck.passed:
                logger.info("[Orchestrator] %s 采纳自动修正版", report.stock_code)
                return verdict.revised, recheck, "revised"

        # 2. 兜底：转为观望 + 零仓位（不给出任何可执行方向，避免带错下单）
        safe = advice.model_copy(deep=True)
        safe.direction = "watch"
        safe.position_pct = 0.0
        safe.entry = None
        safe.stop_loss = None
        safe.take_profit = None
        if not safe.risk_notes:
            safe.risk_notes = "策略未通过校验，已降级为观望"
        else:
            safe.risk_notes = "策略未通过校验，已降级为观望；" + safe.risk_notes
        recheck = self.critic.check_rules(report, safe)
        logger.warning("[Orchestrator] %s 降级为观望（校验未通过且无法自动修正）",
                       report.stock_code)
        return safe, recheck, "fallback"

    # ------------------------------------------------------------------
    # 异常占位
    # ------------------------------------------------------------------
    @staticmethod
    def _empty_result(code: str, error: str) -> PipelineResult:
        """批量执行中单只失败时，构造一个可序列化的占位结果。"""
        from src.agents.schemas import CriticIssue

        verdict = CriticVerdict(
            passed=False,
            issues=[CriticIssue(code="S001", severity="error",
                                message=f"流水线执行失败: {error}",
                                fix="检查数据源与模型可用性", source="system")],
            summary="流水线执行失败，未产出策略建议。",
        )
        return PipelineResult(
            stock_code=code,
            report=AnalystReport(stock_code=code),
            advice=StrategyAdvice(direction="watch", position_pct=0.0),
            verdict=verdict,
            rounds=[],
            n_rounds=0,
            first_pass=False,
            final_pass=False,
            final_source="fallback",
            stop_reason="error",
            timings={},
        )
