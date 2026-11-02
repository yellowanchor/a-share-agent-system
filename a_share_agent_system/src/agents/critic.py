# -*- coding: utf-8 -*-
"""critic.py — 反思校验 Agent（Critic）

设计定位（论文要点）
--------------------
纯靠 LLM 自查不可靠：模型会「自我合理化」，且同一问题多次运行结果不一致，
无法在论文中复现。因此本 Agent 采用**双层校验**：

    第一层  确定性规则（rule）：证据编号、仓位上限、止损方向、数值偏离……
            可复现、可统计、可写进论文的「校验规则表」
    第二层  大模型语义校验（llm）：逻辑一致性、合规表述、极端情景
            作为补充，其结论单独标注 source=llm，不与规则结论混淆

只有 error 级问题才会判定不通过；warning 仅提示。

规则编号表
----------
E001 证据编号越界    E002 无证据引用      E003 引用与声明不一致
P001 仓位超上限      P002 仓位非法
C001 方向与舆情矛盾  C002 观望却有仓位
N001 缺止损/止盈     N002 止损方向错误    N003 止盈方向错误
N004 报价偏离现价过大（疑似幻觉）
T001 缺失效条件      T002 缺持有周期
L001 大模型语义校验发现的问题

用法:
    from src.agents.critic import CriticAgent
    agent = CriticAgent(llm=get_backend("mock"))
    out = agent.run({"report": report, "advice": advice})
    verdict = out["verdict"]        # CriticVerdict
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from src.agents.base import AgentConfig, AgentMessage, BaseAgent
from src.agents.schemas import AnalystReport, CriticIssue, CriticVerdict, StrategyAdvice

logger = logging.getLogger(__name__)

__all__ = ["CriticAgent"]

#: 单票仓位上限（%），与 Strategist 保持一致
MAX_POSITION_PCT = 20.0
#: 报价与现价的最大允许偏离（超过即疑似数值幻觉）
PRICE_DEVIATION = 0.30
#: rationale 中形如 [证据 3] 的引用标记
_CITE_PATTERN = re.compile(r"\[\s*证据\s*(\d+)\s*\]")


class CriticAgent(BaseAgent):
    """反思校验 Agent。

    Attributes:
        max_position_pct: 单票仓位上限。
        price_deviation: 报价相对现价的最大允许偏离比例。
        use_llm: 是否启用第二层语义校验（默认 True）。
    """

    def __init__(
        self,
        llm: Optional[Any] = None,
        config: Optional[AgentConfig] = None,
        max_position_pct: float = MAX_POSITION_PCT,
        price_deviation: float = PRICE_DEVIATION,
        use_llm: bool = True,
    ) -> None:
        """初始化。

        Args:
            llm: LLM 后端（use_llm=False 时可为 None）。
            config: Agent 配置（None 时取 settings.yaml 的 critic 段）。
            max_position_pct: 仓位上限。
            price_deviation: 价格偏离阈值。
            use_llm: 是否启用语义校验。
        """
        from src.config import get

        if config is None:
            config = AgentConfig(
                name="critic",
                temperature=float(get("agents.critic.temperature", 0.0)),
                max_tokens=int(get("agents.critic.max_tokens", 1024)),
            )
        super().__init__(config, llm)
        self.max_position_pct = max_position_pct
        self.price_deviation = price_deviation
        self.use_llm = use_llm and llm is not None

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    def run(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """执行双层校验。

        Args:
            input_data: 需含 `report`（AnalystReport）与 `advice`（StrategyAdvice）。

        Returns:
            {"verdict": CriticVerdict, "prompt": str}
        """
        report = input_data.get("report")
        advice = input_data.get("advice")
        if report is None or advice is None:
            raise ValueError("input_data 必须提供 report 与 advice")
        if isinstance(report, dict):
            report = AnalystReport(**report)
        if isinstance(advice, dict):
            advice = StrategyAdvice(**advice)

        # 第一层：确定性规则
        issues, checked = self._rule_check(report, advice)

        # 第二层：大模型语义校验
        prompt = ""
        llm_used = False
        if self.use_llm:
            prompt = self._build_prompt(report, advice)
            try:
                raw = self.llm.chat(
                    self._system_prompt(), prompt,
                    temperature=self.config.temperature,
                    max_new_tokens=self.config.max_tokens,
                    role=self.config.name,
                )
                from src.utils.json_utils import parse_json_loose
                parsed = parse_json_loose(raw) or {}
                if parsed:
                    llm_used = True
                    issues += self._parse_llm_issues(parsed)
                    checked.append("L001")
            except Exception as exc:
                logger.warning("Critic LLM 校验失败，仅保留规则结论: %s", exc)

        passed = not any(i.severity == "error" for i in issues)
        verdict = CriticVerdict(
            passed=passed,
            issues=issues,
            revised=self._revise(advice, issues) if not passed else None,
            summary=self._summary(passed, issues),
            llm_used=llm_used,
            checked_fields=checked,
        )
        logger.info(
            "[Critic] %s 通过=%s error=%d warning=%d (规则%d项%s)",
            report.stock_code, passed, verdict.n_error, verdict.n_warning,
            len(checked) - (1 if llm_used else 0),
            "+LLM" if llm_used else "",
        )
        return {"verdict": verdict, "prompt": prompt}

    async def process(self, message: AgentMessage) -> AgentMessage:
        """异步入口（基类接口），委托给同步 run。"""
        result = self.run(message.content if isinstance(message.content, dict) else {})
        return await self.send_message(
            message.sender or "strategist",
            result["verdict"].model_dump(),
            {"stage": "critic"},
        )

    # ------------------------------------------------------------------
    # 第一层：确定性规则校验
    # ------------------------------------------------------------------
    def _rule_check(
        self, report: AnalystReport, advice: StrategyAdvice
    ) -> Tuple[List[CriticIssue], List[str]]:
        """执行全部规则，返回 (问题列表, 已执行规则编号)。

        规则顺序与编号表一致；每条规则独立 try，单条异常不影响整体校验。
        """
        issues: List[CriticIssue] = []
        checked: List[str] = []
        valid_ids: Set[int] = {e.idx for e in report.evidence}

        def add(code: str, severity: str, msg: str, fix: str = "") -> None:
            issues.append(CriticIssue(code=code, severity=severity,
                                      message=msg, fix=fix, source="rule"))

        # --- E 系列：证据引用 ---
        checked.append("E001")
        cited = _extract_cites(advice.rationale)
        bad = sorted(c for c in cited if c not in valid_ids)
        if bad and valid_ids:
            add("E001", "error",
                f"推理依据引用了不存在的证据编号 {bad}（可用范围 1-{max(valid_ids)}）",
                "删除越界引用或改为引用检索结果中真实存在的编号")

        bad_ids = sorted(i for i in advice.evidence_ids if i not in valid_ids)
        if bad_ids and valid_ids:
            add("E001", "error",
                f"evidence_ids 含越界编号 {bad_ids}",
                "仅保留真实检索到的证据编号")

        checked.append("E002")
        if advice.direction != "watch" and not (cited or advice.evidence_ids):
            add("E002", "error", "给出交易方向却未引用任何证据",
                "必须引用至少一条检索到的新闻证据")

        checked.append("E003")
        if advice.evidence_ids and cited and set(advice.evidence_ids) != set(cited):
            add("E003", "warning",
                f"声明引用 {sorted(advice.evidence_ids)} 与正文引用 {sorted(cited)} 不一致",
                "统一两处的证据编号")

        # --- P 系列：仓位 ---
        checked.append("P001")
        if advice.position_pct > self.max_position_pct:
            add("P001", "error",
                f"建议仓位 {advice.position_pct:.1f}% 超过单票上限 "
                f"{self.max_position_pct:.0f}%",
                f"将仓位降至 {self.max_position_pct:.0f}% 以内")

        checked.append("P002")
        if advice.position_pct < 0 or advice.position_pct > 100:
            add("P002", "error", f"仓位数值非法: {advice.position_pct}",
                "仓位应在 0-100 之间")

        # --- C 系列：一致性 ---
        checked.append("C001")
        conflict = _sentiment_conflict(report.sentiment, advice.direction)
        if conflict:
            add("C001", "error" if advice.position_pct >= 10 else "warning",
                conflict, "复核舆情信号与策略方向的逻辑链条")

        checked.append("C002")
        if advice.direction == "watch" and advice.position_pct > 0:
            add("C002", "warning",
                f"结论为观望却给出 {advice.position_pct:.1f}% 仓位",
                "观望时仓位应为 0，或改为明确方向")

        # --- N 系列：数值 ---
        close = _f((report.market_snapshot or {}).get("close"))
        checked.append("N001")
        if advice.direction != "watch" and advice.stop_loss is None:
            add("N001", "error", "未设置止损价", "补充止损价位（可参考 ATR 止损）")
        if advice.direction != "watch" and advice.take_profit is None:
            add("N001", "warning", "未设置止盈价", "补充止盈价位")

        checked.append("N002")
        if close and advice.stop_loss:
            if advice.direction == "long" and advice.stop_loss >= close:
                add("N002", "error",
                    f"做多止损 {advice.stop_loss} 不低于现价 {close:.2f}",
                    "做多止损必须低于现价")
            if advice.direction == "short" and advice.stop_loss <= close:
                add("N002", "error",
                    f"做空止损 {advice.stop_loss} 不高于现价 {close:.2f}",
                    "做空止损必须高于现价")

        checked.append("N003")
        if close and advice.take_profit:
            if advice.direction == "long" and advice.take_profit <= close:
                add("N003", "error",
                    f"做多止盈 {advice.take_profit} 不高于现价 {close:.2f}",
                    "做多止盈必须高于现价")
            if advice.direction == "short" and advice.take_profit >= close:
                add("N003", "error",
                    f"做空止盈 {advice.take_profit} 不低于现价 {close:.2f}",
                    "做空止盈必须低于现价")

        checked.append("N004")
        for name, val in (("entry", advice.entry), ("stop_loss", advice.stop_loss),
                          ("take_profit", advice.take_profit)):
            if close and val:
                dev = abs(val - close) / close
                if dev > self.price_deviation:
                    add("N004", "warning",
                        f"{name} 取值 {val} 与现价 {close:.2f} 偏离 {dev:.0%}，"
                        f"疑似数值幻觉",
                        "核对报价来源，或改用以 ATR/支撑阻力计算的参考价")

        # --- T 系列：完整性 ---
        checked.append("T001")
        if advice.direction != "watch" and not advice.invalidation.strip():
            add("T001", "warning", "未说明策略失效条件", "补充明确的失效触发条件")
        checked.append("T002")
        if advice.direction != "watch" and not advice.holding_period.strip():
            add("T002", "warning", "未说明持有周期", "补充建议持有周期")

        return issues, checked

    # ------------------------------------------------------------------
    # 第二层：大模型语义校验
    # ------------------------------------------------------------------
    def _system_prompt(self) -> str:
        """系统提示词（configs/prompts/critic.yaml）。"""
        from src.config import get
        text = get("prompts.critic.system")
        if text:
            return str(text).strip()
        return "你是一位风控审计专家，对策略建议做严格校验，输出严格 JSON。"

    def _build_prompt(self, report: AnalystReport, advice: StrategyAdvice) -> str:
        """组装校验提示词。"""
        lines = [
            f"股票代码: {report.stock_code}　数据截止: {report.as_of}",
            "",
            "【舆情研判】",
            f"- 情感: {report.sentiment}（置信 {report.confidence}）",
            f"- 影响: {report.impact}　主题: {', '.join(report.themes)}",
            f"- 综述: {report.summary}",
            "",
            "【可用证据】",
        ]
        lines += [e.as_prompt_line() for e in report.evidence] or ["（无）"]
        lines += [
            "",
            "【待校验策略】",
            f"- 方向: {advice.direction}　仓位: {advice.position_pct}%",
            f"- 介入: {advice.entry}　止损: {advice.stop_loss}　止盈: {advice.take_profit}",
            f"- 持有周期: {advice.holding_period}",
            f"- 失效条件: {advice.invalidation}",
            f"- 推理: {advice.rationale}",
            f"- 引用证据: {advice.evidence_ids}",
            "",
            "【量价快照】",
        ]
        snap = report.market_snapshot or {}
        for k, cn in (("close", "收盘"), ("ma20", "MA20"), ("rsi14", "RSI14"),
                      ("ret_20d", "近20日涨跌%"), ("annual_vol", "年化波动率")):
            if snap.get(k) is not None:
                lines.append(f"- {cn}: {snap[k]}")
        lines += [
            "",
            "请校验: ①数据引用是否真实 ②策略方向与舆情是否自洽 "
            "③风险敞口是否合理 ④是否存在内幕交易或操纵市场暗示 "
            "⑤极端情景下的潜在损失是否被提示。",
            "严格输出 JSON:",
            '{"issues": [{"code": "L001", "severity": "warning|error|info",'
            ' "message": "问题描述", "fix": "修改建议"}],'
            ' "summary": "总体评价"}',
        ]
        return "\n".join(lines)

    def _parse_llm_issues(self, parsed: Dict[str, Any]) -> List[CriticIssue]:
        """把 LLM 输出的 issues 规范为 CriticIssue（source=llm）。"""
        out: List[CriticIssue] = []
        for item in parsed.get("issues") or []:
            if not isinstance(item, dict):
                continue
            sev = str(item.get("severity", "warning")).lower()
            if sev not in ("error", "warning", "info"):
                sev = "warning"
            out.append(CriticIssue(
                code=str(item.get("code", "L001")),
                severity=sev,
                message=str(item.get("message", "")),
                fix=str(item.get("fix", "")),
                source="llm",
            ))
        return out

    # ------------------------------------------------------------------
    # 修正与摘要
    # ------------------------------------------------------------------
    def _revise(
        self, advice: StrategyAdvice, issues: List[CriticIssue]
    ) -> StrategyAdvice:
        """对可自动修正的问题给出修正版建议（仅修确定性项）。

        可修正项:
            P001 仓位超上限 → 收敛到上限
            C002 观望却有仓位 → 归零
            N002+N003 止损与止盈方向均反 → 两者互换即得正确方向
                     （实测中模型照抄做多参考价却判做空，互换后可自愈）
            E001 越界证据编号 → 剔除越界项
        """
        revised = advice.model_copy(deep=True)
        valid_fix = False
        codes = {i.code for i in issues}

        for i in issues:
            if i.code == "P001":
                revised.position_pct = float(min(advice.position_pct,
                                                 self.max_position_pct))
                valid_fix = True
            elif i.code == "C002":
                revised.position_pct = 0.0
                valid_fix = True

        # 止损与止盈同时落在错误一侧：互换即为正确解
        if ("N002" in codes and "N003" in codes
                and advice.stop_loss is not None
                and advice.take_profit is not None):
            revised.stop_loss, revised.take_profit = (
                advice.take_profit, advice.stop_loss)
            valid_fix = True

        return revised if valid_fix else None

    @staticmethod
    def _summary(passed: bool, issues: List[CriticIssue]) -> str:
        """生成校验摘要。"""
        n_err = sum(1 for i in issues if i.severity == "error")
        n_warn = sum(1 for i in issues if i.severity == "warning")
        if passed and not issues:
            return "校验通过：未发现结构与合规问题。"
        if passed:
            return f"校验通过（有 {n_warn} 项提示，无阻断性问题）。"
        return f"校验不通过：{n_err} 项阻断问题、{n_warn} 项提示，建议修正后采用。"


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _extract_cites(text: str) -> Set[int]:
    """从文本中抽取 [证据 n] 的编号集合。"""
    return {int(m) for m in _CITE_PATTERN.findall(text or "")}


def _sentiment_conflict(sentiment: str, direction: str) -> str:
    """检查舆情方向与策略方向是否矛盾。

    判定口径（保守）：仅当情感置信方向明确相反且非观望时才报。
    """
    if direction == "watch":
        return ""
    if sentiment == "positive" and direction == "short":
        return "舆情判定为利好，却给出做空方向，逻辑链条缺失说明"
    if sentiment == "negative" and direction == "long":
        return "舆情判定为利空，却给出做多方向，逻辑链条缺失说明"
    return ""


def _f(v: Any) -> Optional[float]:
    """安全转 float（None/NaN/非法 → None）。"""
    try:
        if v is None:
            return None
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None
