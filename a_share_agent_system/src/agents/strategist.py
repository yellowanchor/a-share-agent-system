# -*- coding: utf-8 -*-
"""strategist.py — 量化策略 Agent（Strategist）

职责
----
在 Analyst 的研判结论之上，结合量价指标产出**可执行且可审计**的策略建议。
核心约束是「每个判断都要能指回证据或数据」，杜绝凭空臆测。

为什么不让模型直接报价格
------------------------
7B 模型对具体价位没有可靠数值能力（易产生幻觉数字）。因此本 Agent 的做法是：
    1. 先用 ATR / 支撑阻力 / 均线**程序化算出参考价位**（确定性、可复现）
    2. 把参考价位放进提示词，让模型在其附近做选择与说明理由
    3. Critic 再核对最终报价是否落在合理区间（偏离过大即判为幻觉）
这样把数值计算交给代码，把判断与表述交给模型，各取所长。

用法:
    from src.agents.strategist import StrategistAgent
    agent = StrategistAgent(llm=get_backend("mock"))
    out = agent.run({"report": analyst_report})
    advice = out["advice"]      # StrategyAdvice
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.agents.base import AgentConfig, AgentMessage, BaseAgent
from src.agents.schemas import AnalystReport, StrategyAdvice

logger = logging.getLogger(__name__)

__all__ = ["StrategistAgent"]

#: 单票仓位上限（%），与 Critic 的 P001 规则保持一致
MAX_POSITION_PCT = 20.0


class StrategistAgent(BaseAgent):
    """量化策略 Agent。

    Attributes:
        atr_stop_mult: ATR 止损倍数（止损 = 现价 - k×ATR）。
        atr_target_mult: ATR 止盈倍数。
        max_position_pct: 建议仓位上限。
    """

    def __init__(
        self,
        llm: Optional[Any] = None,
        config: Optional[AgentConfig] = None,
        atr_stop_mult: float = 2.0,
        atr_target_mult: float = 3.0,
        max_position_pct: float = MAX_POSITION_PCT,
    ) -> None:
        """初始化。

        Args:
            llm: LLM 后端。
            config: Agent 配置（None 时取 settings.yaml 的 strategist 段）。
            atr_stop_mult: 止损距离 = k × ATR14。
            atr_target_mult: 止盈距离 = k × ATR14。
            max_position_pct: 单票仓位上限。
        """
        from src.config import get

        if config is None:
            config = AgentConfig(
                name="strategist",
                temperature=float(get("agents.strategist.temperature", 0.3)),
                max_tokens=int(get("agents.strategist.max_tokens", 3072)),
            )
        super().__init__(config, llm)
        self.atr_stop_mult = atr_stop_mult
        self.atr_target_mult = atr_target_mult
        self.max_position_pct = max_position_pct

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    def run(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """生成策略建议。

        Args:
            input_data: 需含 `report`（AnalystReport）；
                        可选 `atr_stop_mult` / `atr_target_mult` 覆盖默认值；
                        可选 `feedback`（Critic 上一轮的问题反馈，重试时注入）；
                        可选 `temperature`（重试时由编排层下调以提高收敛性）。

        Returns:
            {"advice": StrategyAdvice, "prompt": str, "reference": dict}
        """
        import time

        t0 = time.time()
        report = input_data.get("report")
        if report is None:
            raise ValueError("input_data 必须提供 report（AnalystReport）")
        if isinstance(report, dict):
            report = AnalystReport(**report)

        stop_mult = float(input_data.get("atr_stop_mult", self.atr_stop_mult))
        target_mult = float(input_data.get("atr_target_mult", self.atr_target_mult))
        feedback = str(input_data.get("feedback") or "")
        temperature = float(input_data.get("temperature", self.config.temperature))

        # 1. 程序化算出参考价位（不依赖模型）
        ref = self._reference_prices(report, stop_mult, target_mult)
        # 2. 组装提示词（重试时把 Critic 的问题反馈一并注入）
        prompt = self._build_prompt(report, ref, feedback)
        system = self._system_prompt()

        raw = ""
        parsed: Dict[str, Any] = {}
        try:
            raw = self.llm.chat(
                system, prompt,
                temperature=temperature,
                max_new_tokens=self.config.max_tokens,
                role=self.config.name,
            )
            from src.utils.json_utils import parse_json_loose
            parsed = parse_json_loose(raw) or {}
        except Exception as exc:
            logger.warning("Strategist LLM 调用失败，转规则路径: %s", exc)

        advice = self._assemble(report, ref, parsed, raw, bool(parsed))
        elapsed = (time.time() - t0) * 1000
        logger.info(
            "[Strategist] %s 方向=%s 仓位=%.1f%% 止损=%s LLM=%s (%.0fms)",
            report.stock_code, advice.direction, advice.position_pct,
            advice.stop_loss, advice.llm_used, elapsed,
        )
        return {"advice": advice, "prompt": prompt, "reference": ref,
                "elapsed_ms": elapsed}

    async def process(self, message: AgentMessage) -> AgentMessage:
        """异步入口（基类接口），委托给同步 run。"""
        content = message.content
        if isinstance(content, AnalystReport):
            content = {"report": content}
        result = self.run(content)
        return await self.send_message(
            message.sender or "analyst",
            result["advice"].model_dump(),
            {"stage": "strategist", "elapsed_ms": result["elapsed_ms"]},
        )

    # ------------------------------------------------------------------
    # 参考价位（确定性计算）
    # ------------------------------------------------------------------
    def _reference_prices(
        self, report: AnalystReport, stop_mult: float, target_mult: float
    ) -> Dict[str, Any]:
        """基于 ATR 与支撑阻力计算参考价位（做多/做空两套）。

        为什么要两套: 实测中模型曾照抄「现价 - 2×ATR」这个做多口径的止损，
        却在结论里选了做空方向，导致止损方向完全相反（被 Critic 的 N002
        拦截）。参考价位必须与方向绑定，否则等于给模型一个错误锚点。

        ATR 缺失时退化为固定百分比（止损 3% / 止盈 6%）。
        """
        snap = report.market_snapshot or {}
        close = _f(snap.get("close"))
        atr = _f(snap.get("atr14"))
        support = _f(snap.get("support"))
        resistance = _f(snap.get("resistance"))

        ref: Dict[str, Any] = {"close": close, "atr14": atr}
        if close is None:
            return ref

        if atr:
            stop_dist, target_dist = stop_mult * atr, target_mult * atr
            ref["basis"] = f"ATR14×{stop_mult}/{target_mult}"
        else:
            stop_dist, target_dist = close * 0.03, close * 0.06
            ref["basis"] = "固定比例 3%/6%（ATR 不可用）"

        # 支撑/阻力夹逼边界（缺省时用 ±50% 兜底，避免区间退化）
        lo = support * 0.98 if support is not None else close * 0.5
        hi = resistance * 1.02 if resistance is not None else close * 1.5

        # 做多：止损在现价下方，止盈在上方
        ref["long_stop_loss"] = round(min(max(close - stop_dist, lo), close), 2)
        ref["long_take_profit"] = round(max(min(close + target_dist, hi), close), 2)
        # 做空：止损在现价上方，止盈在下方
        ref["short_stop_loss"] = round(max(min(close + stop_dist, hi), close), 2)
        ref["short_take_profit"] = round(min(max(close - target_dist, lo), close), 2)
        # 默认口径沿用做多（规则降级路径下若判为做多则直接可用）
        ref["stop_loss"] = ref["long_stop_loss"]
        ref["take_profit"] = ref["long_take_profit"]
        ref["support"] = support
        ref["resistance"] = resistance
        return ref

    # ------------------------------------------------------------------
    # 提示词
    # ------------------------------------------------------------------
    def _system_prompt(self) -> str:
        """系统提示词（configs/prompts/strategist.yaml）。"""
        from src.config import get
        text = get("prompts.strategist.system")
        if text:
            return str(text).strip()
        return (
            "你是一位量化策略研究员，基于舆情报告与量价数据给出可执行的交易策略，"
            "输出严格 JSON。"
        )

    def _build_prompt(
        self,
        report: AnalystReport,
        ref: Dict[str, Any],
        feedback: str = "",
    ) -> str:
        """组装策略提示词（含参考价位、引用规范与可选的 Critic 反馈）。

        Args:
            report: Analyst 研判报告。
            ref: `_reference_prices` 算出的参考价位。
            feedback: 上一轮 Critic 检出的问题描述（重试轮非空）。

        为什么反馈要显式写「其余字段沿用」
        --------------------------------
        只说「你错了请改」会让模型另起炉灶，把原本正确的方向也改掉，
        反而制造新问题。显式约束修改范围，收敛率才高。
        """
        lines: List[str] = [
            f"股票代码: {report.stock_code}",
            f"数据截止日: {report.as_of}",
            "",
            "【Analyst 研判结论】",
            f"- 情感极性: {report.sentiment}（置信度 {report.confidence}）",
            f"- 影响程度: {report.impact}",
            f"- 关键主题: {', '.join(report.themes) or '无'}",
            f"- 结论有效期: {report.valid_until}",
            f"- 综述: {report.summary}",
            "",
            "【可引用的证据】（引用编号必须与下表一致，禁止编造）",
        ]
        if report.evidence:
            lines += [e.as_prompt_line() for e in report.evidence]
        else:
            lines.append("（无证据）")

        lines += ["", "【量价指标】"]
        labels = [
            ("close", "最新收盘"), ("ma20", "MA20"), ("ma60", "MA60"),
            ("rsi14", "RSI14"), ("atr14", "ATR14"), ("ret_20d", "近20日涨跌%"),
            ("vol_ratio", "5日量比"), ("annual_vol", "年化波动率"),
            ("support", "支撑"), ("resistance", "阻力"),
        ]
        snap = report.market_snapshot or {}
        for k, cn in labels:
            if snap.get(k) is not None:
                lines.append(f"- {cn}: {snap[k]}")

        lines += [
            "",
            "【程序化参考价位】（已按 ATR 与支撑阻力算好，"
            f"计算依据: {ref.get('basis', '—')}；最新价 {ref.get('close')}）",
            f"- 若做多(long): 止损 {ref.get('long_stop_loss')}"
            f"（必须低于现价）, 止盈 {ref.get('long_take_profit')}（必须高于现价）",
            f"- 若做空(short): 止损 {ref.get('short_stop_loss')}"
            f"（必须高于现价）, 止盈 {ref.get('short_take_profit')}（必须低于现价）",
            f"- 单票仓位上限: {self.max_position_pct}%",
            "",
            "注意: 止损/止盈的方向必须与 direction 一致，两套价位不可混用。",
        ]
        if feedback:
            lines += [
                "",
                "【上一轮校验未通过，请修正以下问题】",
                feedback,
                "",
                "要求: 只修正上述问题；除此之外的一切字段"
                "（方向、持有周期、推理依据、证据引用）请沿用上一轮取值，"
                "不要另起炉灶重写。",
            ]
        lines += [
            "",
            "请严格输出如下 JSON（不要输出多余文字）:",
            "{",
            '  "direction": "long | short | watch",',
            '  "position_pct": 0到20之间的数字,',
            '  "entry": 介入价或 null,',
            '  "stop_loss": 止损价或 null,',
            '  "take_profit": 止盈价或 null,',
            '  "holding_period": "持有周期",',
            '  "invalidation": "策略失效条件",',
            '  "rationale": "推理依据，引用证据写成 [证据 n]",',
            '  "evidence_ids": [1],',
            '  "risk_notes": "风险提示",',
            '  "confidence": 0到1之间的小数',
            "}",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 结果装配
    # ------------------------------------------------------------------
    def _assemble(
        self,
        report: AnalystReport,
        ref: Dict[str, Any],
        parsed: Dict[str, Any],
        raw: str,
        llm_used: bool,
    ) -> StrategyAdvice:
        """LLM 输出 + 参考价位 → StrategyAdvice（非法值一律收敛到安全区间）。"""
        valid_ids = {e.idx for e in report.evidence}
        ids = [int(i) for i in (parsed.get("evidence_ids") or [])
               if _as_int(i) in valid_ids]

        if not llm_used:
            direction, position = self._rule_direction(report)
            # 参考价位按方向取对应的一套，避免方向不一致
            side = "short" if direction == "short" else "long"
            return StrategyAdvice(
                direction=direction,
                position_pct=position,
                entry=ref.get("close"),
                stop_loss=ref.get(f"{side}_stop_loss"),
                take_profit=ref.get(f"{side}_take_profit"),
                holding_period="5-10个交易日",
                invalidation="跌破参考止损位，或舆情方向反转",
                rationale=(
                    f"舆情为 {report.sentiment}（置信 {report.confidence}），"
                    f"参考价位由 ATR 程序化给出；本建议由规则路径生成，未使用大模型。"
                ),
                evidence_ids=sorted(valid_ids)[:2],
                risk_notes="规则路径生成，建议人工复核后再决策",
                confidence=round(min(0.5, report.confidence), 3),
                llm_used=False,
                raw_llm=raw,
            )

        direction = _norm_choice(parsed.get("direction"), ("long", "short", "watch"), "watch")
        position = self._clamp_position(parsed.get("position_pct"), direction)
        return StrategyAdvice(
            direction=direction,
            position_pct=position,
            entry=_num(parsed.get("entry")),
            stop_loss=_num(parsed.get("stop_loss")),
            take_profit=_num(parsed.get("take_profit")),
            holding_period=str(parsed.get("holding_period", "")),
            invalidation=str(parsed.get("invalidation", "")),
            rationale=str(parsed.get("rationale", "")),
            evidence_ids=ids,
            risk_notes=str(parsed.get("risk_notes", "")),
            confidence=_clamp(_num(parsed.get("confidence")) or 0.5, 0.0, 1.0),
            llm_used=True,
            raw_llm=raw,
        )

    def _rule_direction(self, report: AnalystReport):
        """规则路径的方向与仓位（舆情 + 趋势共振才给仓位）。"""
        snap = report.market_snapshot or {}
        close, ma20 = _f(snap.get("close")), _f(snap.get("ma20"))
        uptrend = bool(close and ma20 and close >= ma20)

        if report.sentiment == "positive" and uptrend:
            return "long", 10.0
        if report.sentiment == "negative" and (not uptrend):
            return "short", 5.0
        return "watch", 0.0

    def _clamp_position(self, v: Any, direction: str) -> float:
        """仓位归一化：非法值按方向给默认，且不超过上限。"""
        p = _num(v)
        if p is None:
            p = {"long": 10.0, "short": 5.0}.get(direction, 0.0)
        if direction == "watch":
            return 0.0
        return float(_clamp(p, 0.0, self.max_position_pct))


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _f(v: Any) -> Optional[float]:
    """安全转 float（None/NaN/非法 → None）。"""
    try:
        if v is None:
            return None
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def _num(v: Any) -> Optional[float]:
    """同 _f，另把布尔排除（避免 True 被当 1.0）。"""
    if isinstance(v, bool):
        return None
    return _f(v)


def _clamp(v: float, lo: float, hi: float) -> float:
    """区间裁剪。"""
    return max(lo, min(hi, v))


def _as_int(v: Any) -> int:
    """尽量转 int，失败返回 -1。"""
    try:
        return int(v)
    except (TypeError, ValueError):
        return -1


def _norm_choice(v: Any, allowed: tuple, default: str) -> str:
    """把模型输出规范到取值域内。"""
    s = str(v).strip().lower()
    return s if s in allowed else default
