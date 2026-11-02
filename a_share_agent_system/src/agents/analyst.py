# -*- coding: utf-8 -*-
"""analyst.py — 舆情分析 Agent（Analyst）

职责
----
把「散落的新闻 + 行情 + 财务」整理成一份**带证据编号**的研判初稿，
供 Strategist 继续加工。它不产出交易决策，只负责事实与情绪的结构化。

设计要点（对应论文第 4 章）
--------------------------
1. **RAG 的正确定位**：Phase 2 实验证明检索增强对单条分类有害（-7 个点），
   因此这里 RAG 不做分类增益，而是做**证据引用**——每条结论可溯源到原文。
2. **情感信号的来源**：用 Phase 2 已验收的词典打标器（90.5% 一致率）对
   检索结果逐条打标，得到可信的情感分布；LLM 只负责文字综合，不负责打标，
   避免「模型自说自话」（幻觉）污染量化信号。
3. **Point-in-Time 对齐**：财务数据严格按 pub_date ≤ as_of 取最近一期，
   杜绝未来函数。
4. **降级可用**：LLM 不可用或输出非法时，走规则路径生成报告（llm_used=False），
   保证流水线不中断——这一点在 Critic 环节会被记为 warning。

用法:
    from src.agents.analyst import AnalystAgent
    agent = AnalystAgent(llm=get_backend("mock"))
    out = agent.run({"stock_code": "600519", "query": "业绩 增长"})
    report = out["report"]      # AnalystReport
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Dict, List, Optional

from src.agents.base import AgentConfig, AgentMessage, BaseAgent
from src.agents.schemas import AnalystReport, Evidence
from src.utils.indicators import MarketSnapshot, fmt_snapshot_lines, summarize_market

logger = logging.getLogger(__name__)

__all__ = ["AnalystAgent"]

# financials_yjbb 中用于报告的关键字段（白名单，规避首列乱码列名）
_FIN_FIELDS = (
    "report_date", "pub_date", "eps", "revenue", "revenue_yoy",
    "net_profit", "net_profit_yoy", "roe", "gross_margin", "bps", "ocf_ps",
)
# 财务字段中文标签（提示词可读 + 单位说明）
_FIN_LABELS = {
    "report_date": "报告期", "pub_date": "披露日", "eps": "每股收益(元)",
    "revenue": "营业收入(元)", "revenue_yoy": "营收同比(%)",
    "net_profit": "净利润(元)", "net_profit_yoy": "净利同比(%)",
    "roe": "ROE(%)", "gross_margin": "毛利率(%)", "bps": "每股净资产(元)",
    "ocf_ps": "每股经营现金流(元)",
}


class AnalystAgent(BaseAgent):
    """舆情分析 Agent。

    Attributes:
        top_k: 语义检索返回条数。
        lookback_days: 行情回看天数（用于计算 MA/RSI/ATR 等指标）。
        snippet_len: 每条证据正文摘录长度（控制提示词规模）。
    """

    def __init__(
        self,
        llm: Optional[Any] = None,
        config: Optional[AgentConfig] = None,
        store: Optional[Any] = None,
        retriever: Optional[Any] = None,
        labeler: Optional[Any] = None,
        top_k: int = 8,
        lookback_days: int = 180,
        snippet_len: int = 160,
        lazy: bool = True,
    ) -> None:
        """初始化。

        Args:
            llm: LLM 后端（None 时自动取配置中的默认后端）。
            config: Agent 配置（None 时用 settings.yaml 的 analyst 段）。
            store: MarketStore 实例（None 时懒加载）。
            retriever: NewsRetriever 实例（None 时懒加载，会占用 GPU）。
            labeler: LexiconLabeler 实例（None 时懒加载）。
            top_k: 检索条数。
            lookback_days: 行情回看天数。
            snippet_len: 证据摘录长度。
            lazy: True 时依赖组件在首次 run 才实例化（省显存）。
        """
        from src.config import get

        if config is None:
            config = AgentConfig(
                name="analyst",
                temperature=float(get("agents.analyst.temperature", 0.1)),
                max_tokens=int(get("agents.analyst.max_tokens", 2048)),
            )
        super().__init__(config, llm)
        self.top_k = int(get("agents.analyst.top_k", top_k))
        self.lookback_days = int(get("agents.analyst.lookback_days", lookback_days))
        self.snippet_len = snippet_len
        self._store = store
        self._retriever = retriever
        self._labeler = labeler
        if not lazy:
            self._ensure_components()

    # ------------------------------------------------------------------
    # 依赖组件（懒加载）
    # ------------------------------------------------------------------
    def _ensure_components(self) -> None:
        """按需实例化 MarketStore / NewsRetriever / LexiconLabeler。"""
        if self._store is None:
            from src.data.storage.market_store import MarketStore
            self._store = MarketStore()
        if self._labeler is None:
            from src.models.sentiment.lexicon_labeler import LexiconLabeler
            self._labeler = LexiconLabeler()
        if self._retriever is None:
            from src.models.rag.retriever import NewsRetriever
            # load_model=False：首次检索再载入 bge-m3，避免空占显存
            self._retriever = NewsRetriever(load_model=False)

    @property
    def retriever(self):
        """检索器（懒加载）。"""
        self._ensure_components()
        return self._retriever

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    def run(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """执行一次舆情分析。

        Args:
            input_data: 需含 `stock_code`；可选 `query` / `as_of` / `top_k`。

        Returns:
            {"report": AnalystReport, "prompt": str, "elapsed_ms": float}
        """
        import time

        t0 = time.time()
        code = str(input_data.get("stock_code", "")).strip()
        if not code:
            raise ValueError("input_data 必须提供 stock_code")

        query = input_data.get("query") or f"{code} 业绩 经营 公告 资金"
        top_k = int(input_data.get("top_k", self.top_k))
        self._ensure_components()

        # --- 1. 行情快照 ---
        df = self._load_daily(code, input_data.get("as_of"))
        snap: MarketSnapshot = summarize_market(df, code=code)
        as_of = snap.as_of or str(input_data.get("as_of", ""))

        # --- 2. 财务快照（Point-in-Time）---
        fin = self._load_financial(code, as_of)

        # --- 3. RAG 检索 + 4. 情感打标 ---
        evidence = self._build_evidence(code, query, as_of, top_k)
        stats = self._sentiment_stats(evidence)

        # --- 5/6. 组装提示词 → LLM ---
        prompt = self._build_prompt(code, as_of, query, evidence, snap, fin, stats)
        system = self._system_prompt()
        raw = ""
        parsed: Dict[str, Any] = {}
        try:
            raw = self.llm.chat(
                system, prompt,
                temperature=self.config.temperature,
                max_new_tokens=self.config.max_tokens,
                role=self.config.name,
            )
            from src.utils.json_utils import parse_json_loose
            parsed = parse_json_loose(raw) or {}
        except Exception as exc:  # 模型不可用 → 降级
            logger.warning("Analyst LLM 调用失败，转规则路径: %s", exc)

        report = self._assemble(code, as_of, query, evidence, snap.to_dict(),
                                fin, stats, parsed, raw, bool(parsed))
        elapsed = (time.time() - t0) * 1000
        logger.info(
            "[Analyst] %s 证据=%d 情感=%s 置信=%.2f LLM=%s (%.0fms)",
            code, report.n_evidence, report.sentiment,
            report.confidence, report.llm_used, elapsed,
        )
        return {"report": report, "prompt": prompt, "elapsed_ms": elapsed}

    async def process(self, message: AgentMessage) -> AgentMessage:
        """异步入口（基类接口），内部委托给同步 run。"""
        result = self.run(message.content if isinstance(message.content, dict)
                          else {"stock_code": str(message.content)})
        return await self.send_message(
            message.sender or "user",
            result["report"].model_dump(),
            {"stage": "analyst", "elapsed_ms": result["elapsed_ms"]},
        )

    # ------------------------------------------------------------------
    # 数据装载
    # ------------------------------------------------------------------
    def _load_daily(self, code: str, as_of: Optional[str]):
        """取回看窗口内的日线（前复权），失败返回空 DataFrame。

        未指定 as_of 时直接取最近 N 条（N = 回看天数 + 指标预热 60 天），
        避免把 1990 年以来的全历史拉进内存。
        """
        import pandas as pd

        try:
            if as_of:
                start = (pd.to_datetime(as_of)
                         - pd.Timedelta(days=int(self.lookback_days * 1.6))
                         ).strftime("%Y-%m-%d")
                return self._store.daily_range(code, start=start, end=as_of, adjust="hfq")
            return self._store.daily_latest(code, adjust="hfq",
                                            n=self.lookback_days + 60)
        except Exception as exc:
            logger.warning("行情查询失败 %s: %s", code, exc)
            return pd.DataFrame()

    def _load_financial(self, code: str, as_of: Optional[str]) -> Dict[str, Any]:
        """按 Point-in-Time 取最近一期业绩报表（pub_date ≤ as_of）。"""
        try:
            df = self._store.latest_financial(code, as_of=as_of)
        except Exception as exc:
            logger.warning("财务查询失败 %s: %s", code, exc)
            return {}
        if df is None or len(df) == 0:
            return {}
        row = df.iloc[0]
        out: Dict[str, Any] = {}
        for f in _FIN_FIELDS:
            if f in df.columns:
                v = row[f]
                out[f] = None if _isna(v) else (str(v)[:10] if "date" in f else round(float(v), 4))
        return out

    def _build_evidence(self, code: str, query: str, as_of: str, top_k: int) -> List[Evidence]:
        """语义检索 + 结构化兜底，逐条打标后编号返回。"""
        six = code.split(".")[-1]
        items: List[Dict] = []
        try:
            items = self.retriever.search(query, stock_code=six, end=as_of, top_k=top_k)
        except Exception as exc:
            logger.warning("语义检索失败，转结构化兜底: %s", exc)

        used_recent = False
        if len(items) < max(2, top_k // 2):
            try:
                recent = self.retriever.recent_by_stock(six, limit=top_k)
                seen = {i.get("id") for i in items}
                items += [r for r in recent if r.get("id") not in seen]
                used_recent = True
            except Exception as exc:
                logger.warning("结构化兜底检索失败: %s", exc)

        evidence: List[Evidence] = []
        for i, it in enumerate(items[:top_k], start=1):
            title = str(it.get("title", ""))
            text = str(it.get("text", ""))
            lab = self._labeler.label(title, text)
            evidence.append(Evidence(
                idx=i,
                title=title,
                source=str(it.get("source", "")),
                publish_time=str(it.get("publish_time", ""))[:19],
                score=it.get("score"),
                sentiment=lab.label,
                confidence=lab.confidence,
                snippet=_snippet(text, self.snippet_len),
                url=str(it.get("url", "")),
                retrieval="recent" if used_recent and it.get("score") is None else "semantic",
            ))
        return evidence

    @staticmethod
    def _sentiment_stats(evidence: List[Evidence]) -> Dict[str, Any]:
        """统计证据情感分布（positive/negative/neutral 计数与占比）。"""
        counts = Counter(e.sentiment for e in evidence)
        n = len(evidence) or 1
        avg_score = (sum(e.confidence for e in evidence if e.sentiment == "positive")
                     - sum(e.confidence for e in evidence if e.sentiment == "negative")) / n
        return {
            "n": len(evidence),
            "positive": counts.get("positive", 0),
            "negative": counts.get("negative", 0),
            "neutral": counts.get("neutral", 0),
            "pos_ratio": round(counts.get("positive", 0) / n, 3),
            "neg_ratio": round(counts.get("negative", 0) / n, 3),
            "net_score": round(avg_score, 3),
        }

    # ------------------------------------------------------------------
    # 提示词
    # ------------------------------------------------------------------
    def _system_prompt(self) -> str:
        """从 configs/prompts/analyst.yaml 读系统提示词（缺失则用内置）。"""
        from src.config import get
        text = get("prompts.analyst.system")
        if text:
            return str(text).strip()
        return (
            "你是一位专业的A股金融舆情分析师。基于提供的新闻、行情与财务数据，"
            "对指定股票做多维度舆情分析，输出严格 JSON。"
        )

    def _build_prompt(
        self,
        code: str,
        as_of: str,
        query: str,
        evidence: List[Evidence],
        snap: MarketSnapshot,
        fin: Dict[str, Any],
        stats: Dict[str, Any],
    ) -> str:
        """组装用户提示词：任务 + 证据 + 行情 + 财务 + 输出格式。"""
        lines: List[str] = [
            f"股票代码: {code}",
            f"数据截止日: {as_of or '未指定'}",
            f"检索查询: {query}",
            "",
            "【检索到的新闻证据】（引用时请使用其编号，如 [证据 1]）",
        ]
        if evidence:
            lines += [e.as_prompt_line() for e in evidence]
            for e in evidence:
                if e.snippet:
                    lines.append(f"    {e.cite()} 摘录: {e.snippet}")
        else:
            lines.append("（未检索到相关新闻，请基于行情与财务数据分析，并明确说明新闻缺失）")

        lines += ["", "【行情快照】（前复权）"]
        lines += fmt_snapshot_lines(snap) or ["（无行情数据）"]

        lines += ["", "【最新一期财务（Point-in-Time，pub_date ≤ 数据截止日）】"]
        if fin:
            for k in _FIN_FIELDS:
                if k in fin and fin[k] is not None:
                    lines.append(f"- {_FIN_LABELS.get(k, k)}: {fin[k]}")
        else:
            lines.append("（无财务数据）")

        lines += [
            "",
            "【证据情感分布（词典弱监督打标结果，非你的输出）】",
            f"- 利好 {stats.get('positive', 0)} 条 / 利空 {stats.get('negative', 0)} 条 "
            f"/ 中性 {stats.get('neutral', 0)} 条",
            f"- 净情绪分: {stats.get('net_score', 0)}",
            "",
            "请严格输出如下 JSON（不要输出多余文字）:",
            "{",
            '  "summary": "200字以内的舆情综述，引用证据时写成 [证据 n]",',
            '  "sentiment": "positive | negative | neutral",',
            '  "confidence": 0到1之间的小数,',
            '  "impact": "major | moderate | minor",',
            '  "themes": ["主题词1", "主题词2"],',
            '  "valid_until": "结论有效期，如 未来10个交易日",',
            '  "evidence_ids": [1, 2]',
            "}",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 结果装配
    # ------------------------------------------------------------------
    def _assemble(
        self,
        code: str,
        as_of: str,
        query: str,
        evidence: List[Evidence],
        snap_dict: Dict[str, Any],
        fin: Dict[str, Any],
        stats: Dict[str, Any],
        parsed: Dict[str, Any],
        raw: str,
        llm_used: bool,
    ) -> AnalystReport:
        """把 LLM 输出与本地信号合并为 AnalystReport（LLM 缺失时规则兜底）。"""
        valid_ids = {e.idx for e in evidence}
        raw_ids = parsed.get("evidence_ids") or []
        evidence_ids = [int(i) for i in raw_ids if _as_int(i) in valid_ids]

        if not llm_used:
            summary, sentiment, impact, themes = self._rule_fallback(evidence, stats)
            confidence = round(min(0.6, 0.2 + abs(stats.get("net_score", 0))), 3)
            valid_until = "未来5个交易日（规则路径，置信度较低）"
        else:
            summary = str(parsed.get("summary", "")).strip()
            sentiment = _norm_choice(parsed.get("sentiment"), ("positive", "negative", "neutral"), "neutral")
            impact = _norm_choice(parsed.get("impact"), ("major", "moderate", "minor"), "moderate")
            themes = [str(t) for t in (parsed.get("themes") or [])][:5]
            try:
                confidence = round(float(parsed.get("confidence", 0.5)), 3)
            except (TypeError, ValueError):
                confidence = 0.5
            confidence = min(max(confidence, 0.0), 1.0)
            valid_until = str(parsed.get("valid_until", "未来10个交易日"))
            if not evidence_ids:
                evidence_ids = sorted(valid_ids)[:2]
            if not summary:
                summary = self._rule_fallback(evidence, stats)[0]

        return AnalystReport(
            stock_code=code,
            as_of=as_of,
            query=query,
            summary=summary,
            sentiment=sentiment,
            confidence=confidence,
            impact=impact,
            themes=themes,
            valid_until=valid_until,
            evidence_ids=evidence_ids,
            evidence=evidence,
            market_snapshot=snap_dict,
            financial_snapshot=fin,
            sentiment_stats=stats,
            llm_used=llm_used,
            raw_llm=raw,
        )

    def _rule_fallback(self, evidence: List[Evidence], stats: Dict[str, Any]):
        """LLM 不可用时的规则化结论（保证流水线不中断）。

        Returns:
            (summary, sentiment, impact, themes)
        """
        pos, neg, neu = (stats.get("positive", 0), stats.get("negative", 0),
                         stats.get("neutral", 0))
        if pos > neg and pos > neu:
            sentiment = "positive"
        elif neg > pos and neg > neu:
            sentiment = "negative"
        else:
            sentiment = "neutral"
        net = abs(stats.get("net_score", 0))
        impact = "major" if net >= 0.5 else ("moderate" if net >= 0.2 else "minor")

        # 主题词：取证据标题中的高频实词（规则路径下的近似替代）
        words: Counter = Counter()
        for e in evidence:
            words.update(_keywords(e.title))
        themes = [w for w, _ in words.most_common(5)]

        summary = (
            f"共检索到 {len(evidence)} 条相关新闻，利好 {pos} 条、利空 {neg} 条、"
            f"中性 {neu} 条，净情绪分 {stats.get('net_score', 0)}。"
            f"综合判定为{'偏正面' if sentiment == 'positive' else '偏负面' if sentiment == 'negative' else '中性'}，"
            f"影响程度 {impact}。（本结论由规则路径生成，未使用大模型）"
        )
        return summary, sentiment, impact, themes


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
_STOPWORDS = set("的了是在和与及为对公司股份公告关于亿元万元年月日个百分点截至"
                 "公司股票投资者关系活动记录表召开举行")


def _keywords(title: str) -> List[str]:
    """从标题中抽候选主题词（2-4 字中文片段，去停用词）。"""
    import re

    out: List[str] = []
    for seg in re.findall(r"[\u4e00-\u9fa5]{2,6}", title or ""):
        if seg in _STOPWORDS:
            continue
        out.append(seg)
    return out


def _snippet(text: str, n: int) -> str:
    """正文摘录（超长截断并加省略号）。"""
    t = (text or "").strip().replace("\n", " ")
    return t if len(t) <= n else t[:n] + "…"


def _isna(v: Any) -> bool:
    """空值判断（兼容 pandas NaN / None）。"""
    try:
        import pandas as pd
        return bool(pd.isna(v))
    except Exception:
        return v is None


def _as_int(v: Any) -> int:
    """尽量转为 int，失败返回 -1。"""
    try:
        return int(v)
    except (TypeError, ValueError):
        return -1


def _norm_choice(v: Any, allowed: tuple, default: str) -> str:
    """把模型输出规范到取值域内。"""
    s = str(v).strip().lower()
    return s if s in allowed else default
