# -*- coding: utf-8 -*-
"""news_gate.py — 新闻闸门（规则否决层）
=========================================

为什么需要闸门：

    每日推荐如果只靠"综合分"，很容易把"业绩爆雷/立案调查/大股东清仓减持"
    这类新闻算成一个普通的负面情感分，然后被趋势与量能两项加权抬高，
    最终仍然给出"买入"。闸门的作用是**一票否决**：无论其他指标多好，
    只要命中灾难性事件，直接拒绝进推荐列表。

    —— 这是本项目"舆情先于量化"的直接体现，也是论文里最容易讲清楚的创新点：
    对比"无闸门 / 有闸门"两组的推荐组合后续 5 日收益。

三层判定：

1. **规则硬闸**（毫秒级）：标题/正文命中灾难性关键词 → ``veto``。
2. **情感闸**：证据加权情感分低于阈值且冲击等级为 major → ``veto``；
   轻度负面 → ``caution``（可进 watch 列表，不进 buy 列表）。
3. **证据充足性**：无新闻或证据 < 2 条 → ``caution``（"没消息"不等于"好消息"）。

用法::

    from src.agents.news_gate import NewsGate
    gate = NewsGate()
    decision = gate.check(report, market_snapshot=snap)
    if decision.verdict == "veto":
        ...  # 从推荐中剔除，理由进 reject_reason
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 关键词表（可扩展；放进配置而不是硬编码在业务逻辑里）
# ---------------------------------------------------------------------------
# 灾难性事件：命中即否决
HARD_VETO_KEYWORDS: List[str] = [
    "退市", "终止上市", "退市风险",
    "立案调查", "立案侦查", "被调查", "证监会调查", "监管函", "问询函",
    "财务造假", "虚增", "资金占用", "内幕交易", "操纵股价", "违规担保",
    "被处罚", "行政处罚", "罚款", "警示函", "公开谴责",
    "商誉减值", "计提减值", "资产减值",
    "业绩预亏", "预亏", "亏损扩大", "由盈转亏",
    "清仓减持", "减持计划", "大股东减持",
    "质押爆仓", "平仓风险", "股权冻结", "被冻结",
    "停牌", "破产", "债务违约", "无法偿还", "逾期",
    "诉讼", "仲裁", "被起诉",
    "实控人变更", "高管辞职", "董事长辞职", "财务总监辞职",
    "造假",
]

# 轻度负面：降级为 caution
SOFT_NEGATIVE_KEYWORDS: List[str] = [
    "下滑", "下降", "不及预期", "低于预期", "承压", "缩减",
    "减持", "解禁", "质押", "商誉", "问询", "关注函",
    "召回", "停产", "减产", "涨价函",
]

# 正面信号（只用于解释，不改变判定）
POSITIVE_KEYWORDS: List[str] = [
    "中标", "增持", "回购", "预增", "扭亏", "获批", "签订", "订单",
    "涨价", "分红", "股权激励", "战略合作", "突破", "新高",
]

_SENTIMENT_VALUE = {"positive": 1.0, "neutral": 0.0, "negative": -1.0}


@dataclass
class GateDecision:
    """闸门判定结果。"""

    verdict: str = "pass"                 # pass / caution / veto
    reason: str = ""
    hits: List[str] = field(default_factory=list)      # 命中的关键词
    sentiment_score: float = 0.0          # 证据加权情感分 [-1, 1]
    n_evidence: int = 0
    layer: str = "rule"                   # rule / sentiment / sufficiency

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "hits": list(self.hits),
            "sentiment_score": round(self.sentiment_score, 4),
            "n_evidence": self.n_evidence,
            "layer": self.layer,
        }


class NewsGate:
    """新闻闸门。

    Args:
        hard_keywords: 灾难性关键词表（默认 :data:`HARD_VETO_KEYWORDS`）。
        soft_keywords: 轻度负面关键词表。
        min_sentiment: 证据加权情感低于该值且冲击 major → veto。
        caution_sentiment: 低于该值 → caution。
        min_evidence: 证据少于该条数 → caution（信息不足）。
        use_llm: 是否叠加 LLM 语义判定（本项目默认关闭，留给消融实验）。
    """

    def __init__(
        self,
        hard_keywords: Optional[List[str]] = None,
        soft_keywords: Optional[List[str]] = None,
        min_sentiment: float = -0.35,
        caution_sentiment: float = -0.15,
        min_evidence: int = 2,
        use_llm: bool = False,
        llm: Optional[Any] = None,
    ) -> None:
        self.hard_keywords = list(hard_keywords or HARD_VETO_KEYWORDS)
        self.soft_keywords = list(soft_keywords or SOFT_NEGATIVE_KEYWORDS)
        self.min_sentiment = float(min_sentiment)
        self.caution_sentiment = float(caution_sentiment)
        self.min_evidence = int(min_evidence)
        self.use_llm = bool(use_llm)
        self.llm = llm

    # ------------------------------------------------------------------
    def check(self, report: Any, market_snapshot: Optional[Dict[str, Any]] = None) -> GateDecision:
        """对一份 AnalystReport 判定是否放行。

        Args:
            report: ``AnalystReport`` 实例（或同构 dict）。
            market_snapshot: 行情快照（当前只用于日志，保留扩展位）。

        Returns:
            :class:`GateDecision`
        """
        evidence = _get(report, "evidence", []) or []
        impact = str(_get(report, "impact", "moderate") or "moderate").lower()

        # ---- 第 1 层：规则硬闸 ----
        hit = self._scan_keywords(evidence)
        if hit:
            return GateDecision(
                verdict="veto",
                reason="命中灾难性新闻关键词：" + "、".join(hit[:3]),
                hits=hit, n_evidence=len(evidence), layer="rule",
            )

        # ---- 第 2 层：情感闸 ----
        score, n = self._sentiment_score(evidence)
        if score <= self.min_sentiment and impact in {"major", "high"}:
            return GateDecision(
                verdict="veto",
                reason=f"证据加权情感 {score:.2f}（≤{self.min_sentiment:.2f}）且冲击等级 {impact}",
                sentiment_score=score, n_evidence=n, layer="sentiment",
            )
        if score <= self.caution_sentiment:
            return GateDecision(
                verdict="caution",
                reason=f"证据偏负面（加权情感 {score:.2f}），降级观察",
                sentiment_score=score, n_evidence=n, layer="sentiment",
            )

        # ---- 第 3 层：证据充足性 ----
        if n < self.min_evidence:
            return GateDecision(
                verdict="caution",
                reason=f"证据仅 {n} 条（<{self.min_evidence}），信息不足，不给买入",
                sentiment_score=score, n_evidence=n, layer="sufficiency",
            )

        # ---- 通过 ----
        return GateDecision(
            verdict="pass",
            reason=f"无灾难性新闻，情感 {score:.2f}，证据 {n} 条",
            sentiment_score=score, n_evidence=n, layer="pass",
        )

    # ------------------------------------------------------------------
    def _scan_keywords(self, evidence: List[Any]) -> List[str]:
        """扫描标题+摘要，返回命中的灾难性关键词（去重保序）。"""
        hits: List[str] = []
        for ev in evidence:
            text = f"{_get(ev, 'title', '')} {_get(ev, 'snippet', '')}"
            for kw in self.hard_keywords:
                if kw in text and kw not in hits:
                    hits.append(kw)
        return hits

    def _sentiment_score(self, evidence: List[Any]) -> tuple[float, int]:
        """证据加权情感分：按 confidence 加权，snippet 命中轻度负面再扣 0.1。"""
        total_w = 0.0
        acc = 0.0
        for ev in evidence:
            label = str(_get(ev, "sentiment", "neutral") or "neutral").lower()
            conf = float(_get(ev, "confidence", 0.0) or 0.0)
            w = max(0.05, min(1.0, conf))      # 低置信度也给一点权重，避免全被抹掉
            val = _SENTIMENT_VALUE.get(label, 0.0)
            text = f"{_get(ev, 'title', '')} {_get(ev, 'snippet', '')}"
            if any(kw in text for kw in self.soft_keywords):
                val -= 0.2
            acc += val * w
            total_w += w
        if total_w <= 0:
            return 0.0, 0
        return max(-1.0, min(1.0, acc / total_w)), len(evidence)


# ---------------------------------------------------------------------------
def _get(obj: Any, name: str, default: Any = None) -> Any:
    """兼容 pydantic 模型 / dict / 普通对象的字段读取。"""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def scan_text(text: str, gates: Optional[NewsGate] = None) -> Dict[str, Any]:
    """给单条文本做快速扫描（前端"证据链"高亮、以及新闻入库时预筛都可复用）。"""
    g = gates or NewsGate()
    hits = [kw for kw in g.hard_keywords if kw in (text or "")]
    soft = [kw for kw in g.soft_keywords if kw in (text or "")]
    pos = [kw for kw in POSITIVE_KEYWORDS if kw in (text or "")]
    return {"hard": hits, "soft": soft, "positive": pos,
            "verdict": "veto" if hits else ("caution" if soft else "pass")}
