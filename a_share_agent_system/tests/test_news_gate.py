"""新闻闸门（NewsGate）单元测试：三层判定必须可预测、可解释。

覆盖：
  * 兜底路径提到灾难词 → veto（哪怕情感分不低）；
  * 纯负面且高强度舆情 → veto，弱负面 → caution；
  * 证据不足 → caution；
  * 正常证据 → pass；
  * scan_text 对单段文本的关键词扫描。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.agents.news_gate import HARD_VETO_KEYWORDS, NewsGate, scan_text


def _ev(title: str, sentiment: str = "neutral", confidence: float = 0.8,
        impact: str = "low") -> SimpleNamespace:
    return SimpleNamespace(title=title, text=title, sentiment=sentiment,
                           confidence=confidence, impact=impact, source="test",
                           publish_time="2026-11-20 10:00:00", url="")


def _report(evidence, sentiment: str = "neutral", impact: str = "low"):
    return SimpleNamespace(evidence=evidence, sentiment=sentiment,
                           impact=impact, stock_code="600519")


def test_hard_keyword_veto():
    """出现"退市/立案调查"等灾难词 → 直接否决。"""
    gate = NewsGate()
    word = "立案调查"
    assert word in HARD_VETO_KEYWORDS, "测试假设该词在硬否决表里"
    d = gate.check(_report([_ev(f"公司被{word}，股价承压"), _ev("公司被" + word)]))
    assert d.verdict == "veto"
    assert word in d.hits
    assert "硬性" in d.reason or "关键" in d.reason or d.reason


def test_weak_negative_is_caution_not_veto():
    """轻度负面舆情 → 谨慎（caution），不否决。"""
    gate = NewsGate(min_sentiment=-0.35, caution_sentiment=-0.15)
    d = gate.check(_report([_ev("业绩小幅下滑", "negative", 0.9),
                            _ev("行业需求走弱", "negative", 0.9)],
                           sentiment="negative", impact="low"))
    assert d.verdict == "caution"


def test_strong_negative_with_high_impact_is_veto():
    """情感分很低 + impact=high → 否决。"""
    gate = NewsGate(min_sentiment=-0.35)
    d = gate.check(_report([_ev("业绩大幅预亏", "negative", 0.95),
                            _ev("机构大幅下调评级", "negative", 0.95),
                            _ev("产品降价挤压毛利", "negative", 0.9)],
                           sentiment="negative", impact="high"))
    assert d.verdict == "veto"


def test_insufficient_evidence_is_caution():
    """证据不足（少于 min_evidence）→ 谨慎，避免"没新闻也敢推荐"。"""
    gate = NewsGate(min_evidence=2)
    d = gate.check(_report([_ev("公司正常经营", "neutral")]))
    assert d.verdict == "caution"
    assert d.n_evidence == 1


def test_clean_evidence_passes():
    """充足且正面 → 通过。"""
    gate = NewsGate()
    d = gate.check(_report([_ev("净利润同比增长 30%", "positive", 0.9),
                            _ev("获大额订单", "positive", 0.85),
                            _ev("机构上调目标价", "positive", 0.8)],
                           sentiment="positive", impact="medium"))
    assert d.verdict == "pass"
    assert d.sentiment_score > 0


def test_empty_report_is_caution():
    """完全无证据 → 谨慎（而不是 pass）。"""
    gate = NewsGate()
    d = gate.check(_report([], sentiment="neutral"))
    assert d.verdict == "caution"
    assert d.n_evidence == 0


def test_scan_text_reports_keyword_buckets():
    """单段文本扫描：同时命中的硬否决词与正面词都要报出来。"""
    out = scan_text("公司获大额订单，但控股股东被立案调查")
    assert "立案调查" in out["hard"]
    assert out["positive"]
