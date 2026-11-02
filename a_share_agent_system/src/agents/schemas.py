# -*- coding: utf-8 -*-
"""schemas.py — 多智能体间传递的结构化数据契约

为什么要单独定义
----------------
三 Agent 协作的核心风险是「上游输出格式漂移导致下游解析崩溃」。
这里用 Pydantic 把每个 Agent 的输出固定为显式契约：
    - 字段缺失/类型错误在入口即报错，不会静默传到下一环
    - Critic 可基于契约做确定性校验（证据编号、仓位上限、方向一致性）
    - 论文中可直接引用本节作为「Agent 通信协议」设计

协议分层：
    Evidence       检索证据（RAG 输出，带编号，供下游引用）
    AnalystReport  Analyst → Strategist
    StrategyAdvice Strategist → Critic
    CriticVerdict  Critic → 用户/前端
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

__all__ = [
    "Evidence",
    "AnalystReport",
    "StrategyAdvice",
    "CriticIssue",
    "CriticVerdict",
    "SENTIMENT_LABELS",
    "DIRECTIONS",
]


#: 情感标签取值域（与 Phase 2 词典/SFT 输出口径一致）
SENTIMENT_LABELS = ("positive", "negative", "neutral")
#: 策略方向取值域
DIRECTIONS = ("long", "short", "watch")


class Evidence(BaseModel):
    """一条可引用的检索证据。

    Attributes:
        idx: 证据编号，从 1 开始，供报告正文以 [证据 n] 形式引用。
        title: 新闻标题。
        source: 来源站点。
        publish_time: 发布时间字符串。
        score: 语义相似度（0-1），结构化兜底检索时为 None。
        sentiment: 词典弱监督打标结果（positive/negative/neutral）。
        confidence: 打标置信度（0-1）。
        snippet: 正文摘录（截断后，控制提示词长度）。
        url: 原文链接（可溯源）。
        retrieval: 检索方式（semantic / recent）。
    """

    idx: int = Field(ge=1, description="证据编号（从 1 开始）")
    title: str = ""
    source: str = ""
    publish_time: str = ""
    score: Optional[float] = None
    sentiment: str = "neutral"
    confidence: float = 0.0
    snippet: str = ""
    url: str = ""
    retrieval: str = "semantic"

    def cite(self) -> str:
        """返回引用标记，如 [证据 3]。"""
        return f"[证据 {self.idx}]"

    def as_prompt_line(self) -> str:
        """渲染为提示词中的一行（含情感标签与来源）。"""
        score = f"{self.score:.3f}" if self.score is not None else "—"
        return (
            f"{self.cite()} ({self.publish_time}｜{self.source}｜相似度{score}｜"
            f"情感:{self.sentiment}) {self.title}"
        )


class AnalystReport(BaseModel):
    """Analyst 输出：舆情研判报告（含证据链与量价快照）。

    Attributes:
        stock_code: 6 位股票代码。
        as_of: 数据截止日（Point-in-Time 对齐基准）。
        query: 本次检索查询串。
        summary: 舆情综述（200 字以内）。
        sentiment: 整体情感极性 positive/negative/neutral。
        confidence: 研判置信度 0-1。
        impact: 影响程度 major/moderate/minor。
        themes: 关键主题词（≤5 个）。
        valid_until: 结论有效窗口（YYYY-MM-DD 或相对描述）。
        evidence_ids: summary 中实际引用的证据编号。
        evidence: 证据列表。
        market_snapshot: 量价指标快照（用于 Critic 数值核对）。
        financial_snapshot: PIT 财务快照。
        sentiment_stats: 证据情感分布统计。
        llm_used: 是否由 LLM 生成（False 表示走了规则降级路径）。
        raw_llm: LLM 原始输出（审计留痕）。
    """

    stock_code: str
    as_of: str = ""
    query: str = ""
    summary: str = ""
    sentiment: str = "neutral"
    confidence: float = 0.0
    impact: str = "moderate"
    themes: List[str] = Field(default_factory=list)
    valid_until: str = ""
    evidence_ids: List[int] = Field(default_factory=list)
    evidence: List[Evidence] = Field(default_factory=list)
    market_snapshot: Dict[str, Any] = Field(default_factory=dict)
    financial_snapshot: Dict[str, Any] = Field(default_factory=dict)
    sentiment_stats: Dict[str, Any] = Field(default_factory=dict)
    llm_used: bool = False
    raw_llm: str = ""

    @property
    def n_evidence(self) -> int:
        """证据条数。"""
        return len(self.evidence)

    def evidence_by_id(self, idx: int) -> Optional[Evidence]:
        """按编号取证据，越界返回 None。"""
        for e in self.evidence:
            if e.idx == idx:
                return e
        return None


class StrategyAdvice(BaseModel):
    """Strategist 输出：可执行策略建议。

    Attributes:
        direction: long（做多）/ short（做空）/ watch（观望）。
        position_pct: 建议仓位百分比（0-100），单票上限 20%。
        entry: 建议介入价位，None 表示不指定。
        stop_loss: 止损价。
        take_profit: 止盈价。
        holding_period: 建议持有周期描述。
        invalidation: 策略失效条件。
        rationale: 推理依据（须引用证据编号）。
        evidence_ids: 引用的证据编号。
        risk_notes: 风险提示。
        confidence: 策略置信度 0-1。
        llm_used / raw_llm: 同 AnalystReport。
    """

    direction: str = "watch"
    position_pct: float = 0.0
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    holding_period: str = ""
    invalidation: str = ""
    rationale: str = ""
    evidence_ids: List[int] = Field(default_factory=list)
    risk_notes: str = ""
    confidence: float = 0.0
    llm_used: bool = False
    raw_llm: str = ""


class CriticIssue(BaseModel):
    """Critic 检出的单个问题。

    Attributes:
        code: 规则编号（如 P001 仓位超限），便于定位与统计。
        severity: error / warning / info。
        message: 问题描述。
        fix: 修复建议（可为空）。
        source: rule（确定性规则） / llm（模型语义校验）。
    """

    code: str
    severity: str = "warning"
    message: str = ""
    fix: str = ""
    source: str = "rule"


class CriticVerdict(BaseModel):
    """Critic 输出：校验结论。

    Attributes:
        passed: 是否通过（无 error 级问题）。
        issues: 问题列表。
        revised: 修正后的策略建议（Critic 有修正时提供）。
        summary: 校验摘要。
        llm_used: 是否使用了 LLM 语义校验。
        checked_fields: 实际执行过的校验项编号。
    """

    passed: bool = True
    issues: List[CriticIssue] = Field(default_factory=list)
    revised: Optional[StrategyAdvice] = None
    summary: str = ""
    llm_used: bool = False
    checked_fields: List[str] = Field(default_factory=list)

    @property
    def n_error(self) -> int:
        """error 级问题数。"""
        return sum(1 for i in self.issues if i.severity == "error")

    @property
    def n_warning(self) -> int:
        """warning 级问题数。"""
        return sum(1 for i in self.issues if i.severity == "warning")
