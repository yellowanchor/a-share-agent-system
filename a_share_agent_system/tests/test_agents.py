# -*- coding: utf-8 -*-
"""test_agents.py — Phase 3 三 Agent 单元测试

覆盖范围
--------
1. 词典回归      Golden Set 200 条一致率（Phase 2 结论的守护测试）
2. 技术指标      MA/RSI/ATR/涨跌幅 的解析解比对
3. JSON 容错     围栏 / 前后文字 / 截断 / 中文引号
4. Mock 路由     三个角色返回各自预设
5. Analyst       真实数据端到端（真实检索 + Mock LLM）
6. Strategist    ATR 参考价位的确定性计算
7. Critic        违规输入必须触发 E001/P001/N002 等规则
8. 降级路径      LLM 返回非法内容时流水线不中断

运行:
    pytest tests/test_agents.py -v
    pytest tests/test_agents.py -v -m "not slow"     # 跳过真实检索（约 30s）
    python tests/test_agents.py                      # 无 pytest 时顺序执行
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agents.analyst import AnalystAgent  # noqa: E402
from src.agents.critic import CriticAgent  # noqa: E402
from src.agents.schemas import AnalystReport, Evidence, StrategyAdvice  # noqa: E402
from src.agents.strategist import StrategistAgent  # noqa: E402
from src.models.llm import MockLLMBackend, get_backend  # noqa: E402
from src.utils.indicators import atr, rsi, sma, summarize_market  # noqa: E402
from src.utils.json_utils import parse_json_loose  # noqa: E402

GOLDEN_PATH = ROOT / "data" / "processed" / "golden_set_200.parquet"


# ===========================================================================
# 1. 词典回归（Phase 2 结论守护）
# ===========================================================================
@pytest.mark.slow
def test_lexicon_golden_set_agreement():
    """Golden Set 200 条上，词典打标与人工标注一致率应 ≥ 88%。

    Phase 2 实测为 90.5%，此处放宽到 88% 以避免词典微调造成脆性失败；
    若低于该值说明词典被破坏，需立即排查。
    """
    if not GOLDEN_PATH.exists():
        pytest.skip(f"Golden Set 不存在: {GOLDEN_PATH}")

    from src.models.sentiment.lexicon_labeler import LexiconLabeler

    df = pd.read_parquet(GOLDEN_PATH)
    labeler = LexiconLabeler()
    hit = sum(
        1 for _, r in df.iterrows()
        if labeler.label(str(r["title"]), str(r.get("text", ""))).label == r["human_label"]
    )
    acc = hit / len(df)
    assert acc >= 0.88, f"词典一致率退化: {acc:.3f}（阈值 0.88, Phase2 实测 0.905）"


# ===========================================================================
# 2. 技术指标
# ===========================================================================
def _mk_df(n: int = 80, start: float = 100.0, step: float = 1.0) -> pd.DataFrame:
    """构造等差上涨的日线（便于手算校验）。"""
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=n),
        "open": [start + i * step for i in range(n)],
        "high": [start + i * step + 2 for i in range(n)],
        "low": [start + i * step - 2 for i in range(n)],
        "close": [start + i * step for i in range(n)],
        "volume": [1_000_000 + i * 1000 for i in range(n)],
    })


def test_indicators_against_closed_form():
    """MA/RSI/涨跌幅 与解析解一致。"""
    df = _mk_df(80, 100.0, 1.0)
    close = df["close"]

    assert abs(sma(close, 5) - 177.0) < 1e-6        # (175+176+177+178+179)/5
    assert rsi(close, 14) == pytest.approx(100.0)   # 单调上涨 → 无跌幅
    snap = summarize_market(df, code="TEST")
    assert snap.close == 179.0
    assert abs(snap.ret_5d - (179.0 / 174.0 - 1) * 100) < 1e-6
    assert snap.n_bars == 80


def test_atr_on_constant_range():
    """高低价恒定差 4 时，ATR14 应收敛到 4（涨幅不超过真实波幅）。"""
    df = _mk_df(40, 100.0, 0.0)     # 价格恒定，high-low 恒为 4
    assert atr(df, 14) == pytest.approx(4.0, abs=1e-6)


def test_snapshot_handles_empty():
    """空 DataFrame 不应抛异常，样本数记为 0。"""
    snap = summarize_market(pd.DataFrame(), code="EMPTY")
    assert snap.n_bars == 0
    assert snap.close != snap.close      # NaN


# ===========================================================================
# 3. JSON 容错解析
# ===========================================================================
@pytest.mark.parametrize("raw", [
    '{"a": 1}',
    '```json\n{"a": 1}\n```',
    '好的，结果如下：\n{"a": 1}\n以上。',
    '{"a": 1,}',                      # 尾逗号
    '{"a": “1”}',                      # 中文引号
])
def test_parse_json_loose_variants(raw):
    """各种退化输出都应解析成功。"""
    assert parse_json_loose(raw) == {"a": 1} or parse_json_loose(raw)["a"] in (1, "1")


def test_parse_json_loose_truncated():
    """尾部截断时尽量补救（返回 None 也可接受，但不得抛异常）。"""
    out = parse_json_loose('{"summary": "半句话", "sentiment": "posi')
    assert out is None or isinstance(out, dict)


def test_parse_json_loose_garbage():
    """完全无关文本返回 None（交由上层降级）。"""
    assert parse_json_loose("抱歉，我无法完成该分析任务。") is None


# ===========================================================================
# 4. Mock 后端路由
# ===========================================================================
def test_mock_role_routing():
    """按 role 路由到各自预设，避免提示词关键词误判。"""
    llm = MockLLMBackend()
    a = llm.chat("sys", "u", role="analyst")
    s = llm.chat("sys", "u", role="strategist")
    c = llm.chat("sys", "u", role="critic")
    assert "summary" in a and "direction" not in a
    assert "direction" in s
    assert "issues" in c
    assert len(llm.calls) == 3


def test_mock_invalid_mode():
    """invalid 模式返回不可解析文本，用于演练降级路径。"""
    llm = MockLLMBackend(mode="invalid")
    assert parse_json_loose(llm.chat("s", "u", role="analyst")) is None


def test_get_backend_factory():
    """工厂函数按名称返回对应类型；未知名称报错。"""
    assert isinstance(get_backend("mock"), MockLLMBackend)
    with pytest.raises(ValueError):
        get_backend("nonexistent-backend")


# ===========================================================================
# 5. Analyst 端到端（真实数据 + Mock LLM）
# ===========================================================================
class _StubRetriever:
    """离线检索桩：返回固定新闻，避免加载 bge-m3。"""

    def search(self, query, stock_code=None, start=None, end=None, top_k=5, **kw):
        return [
            {"id": f"n{i}", "title": f"测试利好新闻{i}", "text": "公司业绩大幅增长，净利润同比增长30%",
             "source": "test", "stock_code": stock_code or "600519",
             "publish_time": "2026-08-15 10:00:00", "score": 0.9 - i * 0.01}
            for i in range(1, min(top_k, 3) + 1)
        ]

    def recent_by_stock(self, stock_code, limit=10):
        return [
            {"id": "r1", "title": "测试利空新闻", "text": "公司涉嫌违规被立案调查，存在退市风险",
             "source": "test", "stock_code": stock_code,
             "publish_time": "2026-08-10 09:00:00", "score": None}
        ]


@pytest.mark.slow
def test_analyst_end_to_end_real_data():
    """真实 DuckDB 行情/财务 + 桩检索 + Mock LLM，验证产出结构与 PIT 对齐。"""
    agent = AnalystAgent(llm=get_backend("mock"), retriever=_StubRetriever())
    out = agent.run({"stock_code": "600519", "as_of": "2026-08-20"})

    report: AnalystReport = out["report"]
    assert report.stock_code == "600519"
    assert report.as_of == "2026-08-20"
    assert report.n_evidence >= 3
    assert report.llm_used is True
    assert report.sentiment in ("positive", "negative", "neutral")
    assert 0.0 <= report.confidence <= 1.0
    # Point-in-Time：披露日不得晚于截止日
    if report.financial_snapshot.get("pub_date"):
        assert str(report.financial_snapshot["pub_date"]) <= "2026-08-20"
    # 证据编号连续且唯一
    assert [e.idx for e in report.evidence] == list(range(1, report.n_evidence + 1))
    # 行情快照含关键指标
    assert report.market_snapshot.get("close") is not None


def test_analyst_fallback_when_llm_invalid():
    """LLM 输出非法时走规则路径，流水线不中断。"""
    agent = AnalystAgent(llm=MockLLMBackend(mode="invalid"), retriever=_StubRetriever())
    report = agent.run({"stock_code": "600519", "as_of": "2026-08-20"})["report"]
    assert report.llm_used is False
    assert report.summary          # 规则路径也必须给出综述
    assert report.n_evidence >= 3


def test_analyst_requires_stock_code():
    """缺少 stock_code 应明确报错，而非静默返回空结果。"""
    agent = AnalystAgent(llm=get_backend("mock"), retriever=_StubRetriever())
    with pytest.raises(ValueError):
        agent.run({})


# ===========================================================================
# 6. Strategist 参考价位
# ===========================================================================
def _mk_report(sentiment="neutral", close=100.0, atr14=5.0, **snap) -> AnalystReport:
    """构造一个最小可用的 AnalystReport。"""
    market = {"close": close, "atr14": atr14, "support": 90.0, "resistance": 120.0}
    market.update(snap)
    return AnalystReport(
        stock_code="600519", as_of="2026-08-20", sentiment=sentiment,
        confidence=0.7, impact="moderate", themes=["业绩"],
        summary="测试综述 [证据 1]", evidence_ids=[1],
        evidence=[Evidence(idx=1, title="t", sentiment=sentiment,
                           publish_time="2026-08-15 10:00:00", score=0.9)],
        market_snapshot=market,
    )


def test_reference_prices_use_atr():
    """做多：止损 = 现价 - 2×ATR，止盈 = 现价 + 3×ATR，并被支撑阻力夹逼。"""
    agent = StrategistAgent(llm=get_backend("mock"))
    ref = agent._reference_prices(_mk_report(close=100.0, atr14=5.0), 2.0, 3.0)
    assert ref["long_stop_loss"] == 90.0       # 100 - 2×5，且不低于支撑×0.98=88.2
    assert ref["long_take_profit"] == 115.0    # 100 + 3×5，且不高于阻力×1.02=122.4
    assert ref["basis"].startswith("ATR14")
    # 默认口径沿用做多
    assert ref["stop_loss"] == ref["long_stop_loss"]


def test_reference_prices_short_side_is_mirrored():
    """做空参考价必须镜像：止损在现价上方、止盈在下方（防模型照抄做多价）。"""
    agent = StrategistAgent(llm=get_backend("mock"))
    ref = agent._reference_prices(_mk_report(close=100.0, atr14=5.0), 2.0, 3.0)
    assert ref["short_stop_loss"] == 110.0     # 100 + 2×5
    assert ref["short_take_profit"] == 88.2    # 100 - 3×5 = 85，被支撑×0.98=88.2 抬升
    assert ref["short_stop_loss"] > ref["close"] > ref["short_take_profit"]


def test_reference_prices_fallback_without_atr():
    """ATR 缺失时退化为 3%/6% 固定比例。"""
    agent = StrategistAgent(llm=get_backend("mock"))
    ref = agent._reference_prices(_mk_report(close=100.0, atr14=None), 2.0, 3.0)
    assert ref["stop_loss"] == 97.0
    assert ref["take_profit"] == 106.0
    assert "固定比例" in ref["basis"]


def test_position_clamped_to_cap():
    """模型给出 60% 仓位时收敛到上限 20%；观望时归零。"""
    agent = StrategistAgent(llm=get_backend("mock"))
    assert agent._clamp_position(60, "long") == 20.0
    assert agent._clamp_position(-5, "long") == 0.0
    assert agent._clamp_position(10, "watch") == 0.0
    assert agent._clamp_position("abc", "long") == 10.0     # 非法值 → 默认 10%


# ===========================================================================
# 7. Critic 规则校验
# ===========================================================================
def test_critic_flags_position_breach_and_bad_citations():
    """越界引用 + 超仓 + 做多止损高于现价 → 必须判不通过并给出编号。"""
    report = _mk_report(sentiment="neutral", close=100.0)
    advice = StrategyAdvice(
        direction="long", position_pct=50.0, entry=100.0,
        stop_loss=110.0, take_profit=120.0,     # 做多止损高于现价（N002）
        rationale="依据 [证据 9] 判断",           # 编号 9 不存在（E001）
        evidence_ids=[9],
        invalidation="跌破止损", holding_period="10个交易日",
    )
    critic = CriticAgent(llm=get_backend("mock"))
    verdict = critic.run({"report": report, "advice": advice})["verdict"]

    codes = {i.code for i in verdict.issues}
    assert not verdict.passed
    assert "P001" in codes        # 仓位超上限
    assert "E001" in codes        # 证据编号越界
    assert "N002" in codes        # 止损方向错误
    assert verdict.revised is not None and verdict.revised.position_pct == 20.0


def test_critic_revises_reversed_stop_and_take():
    """做空却把止损/止盈写反时，互换两者即为正确解（Critic 自动修正）。

    对应真实案例：模型照抄「现价 - 2×ATR」的做多参考价却判了做空方向。
    """
    report = _mk_report(sentiment="negative", close=100.0)
    advice = StrategyAdvice(
        direction="short", position_pct=10.0, entry=100.0,
        stop_loss=95.0, take_profit=112.0,     # 两者均在错误一侧
        rationale="舆情利空 [证据 1]", evidence_ids=[1],
        invalidation="突破112", holding_period="10个交易日",
    )
    verdict = CriticAgent(llm=get_backend("mock")).run(
        {"report": report, "advice": advice})["verdict"]
    codes = {i.code for i in verdict.issues}
    assert {"N002", "N003"} <= codes
    assert verdict.revised is not None
    assert verdict.revised.stop_loss == 112.0      # 互换后高于现价 ✓
    assert verdict.revised.take_profit == 95.0     # 互换后低于现价 ✓


def test_critic_passes_clean_advice():
    """合规建议应通过校验（不产生 error 级问题）。"""
    report = _mk_report(sentiment="positive", close=100.0)
    advice = StrategyAdvice(
        direction="long", position_pct=10.0, entry=100.0,
        stop_loss=95.0, take_profit=115.0,
        rationale="舆情利好，站上MA20 [证据 1]",
        evidence_ids=[1], invalidation="跌破95",
        holding_period="10个交易日",
    )
    verdict = CriticAgent(llm=get_backend("mock")).run(
        {"report": report, "advice": advice})["verdict"]
    assert verdict.n_error == 0
    assert verdict.passed


def test_critic_detects_sentiment_conflict():
    """舆情利空却做多 → C001。"""
    report = _mk_report(sentiment="negative", close=100.0)
    advice = StrategyAdvice(
        direction="long", position_pct=10.0, stop_loss=95.0, take_profit=115.0,
        rationale="逆向布局 [证据 1]", evidence_ids=[1],
        invalidation="跌破止损", holding_period="5个交易日",
    )
    verdict = CriticAgent(llm=get_backend("mock")).run(
        {"report": report, "advice": advice})["verdict"]
    assert "C001" in {i.code for i in verdict.issues}


def test_critic_detects_price_hallucination():
    """报价偏离现价 300% → N004 疑似幻觉。"""
    report = _mk_report(sentiment="neutral", close=100.0)
    advice = StrategyAdvice(
        direction="long", position_pct=5.0, entry=400.0,
        stop_loss=95.0, take_profit=115.0,
        rationale="测试 [证据 1]", evidence_ids=[1],
        invalidation="跌破止损", holding_period="5个交易日",
    )
    verdict = CriticAgent(llm=get_backend("mock")).run(
        {"report": report, "advice": advice})["verdict"]
    assert "N004" in {i.code for i in verdict.issues}


def test_critic_rule_only_mode():
    """use_llm=False 时应跳过语义层，且 L001 不出现在已执行项里。"""
    report = _mk_report(sentiment="positive", close=100.0)
    advice = StrategyAdvice(
        direction="long", position_pct=10.0, stop_loss=95.0, take_profit=115.0,
        rationale="测试 [证据 1]", evidence_ids=[1],
        invalidation="跌破止损", holding_period="5个交易日",
    )
    verdict = CriticAgent(llm=None, use_llm=False).run(
        {"report": report, "advice": advice})["verdict"]
    assert verdict.llm_used is False
    assert "L001" not in verdict.checked_fields
    assert verdict.passed


# ===========================================================================
# 8. 三 Agent 串联一致性
# ===========================================================================
def test_pipeline_consistency_with_mocks():
    """Mock 后端下三 Agent 串联，前一级输出可直接喂给下一级。"""
    report = _mk_report(sentiment="neutral", close=100.0)
    advice = StrategistAgent(llm=get_backend("mock")).run({"report": report})["advice"]
    verdict = CriticAgent(llm=get_backend("mock")).run(
        {"report": report, "advice": advice})["verdict"]
    assert advice.direction in ("long", "short", "watch")
    assert isinstance(verdict.passed, bool)


if __name__ == "__main__":  # pragma: no cover
    """无 pytest 时顺序执行（跳过 slow 用例）。"""
    funcs = [obj for name, obj in sorted(globals().items())
             if name.startswith("test_") and callable(obj)
             and not getattr(obj, "pytestmark", None)]
    failed = 0
    for f in funcs:
        try:
            f()
            print(f"PASS  {f.__name__}")
        except Exception as exc:
            failed += 1
            print(f"FAIL  {f.__name__}: {exc}")
    print(f"\n{len(funcs) - failed}/{len(funcs)} passed")
    sys.exit(1 if failed else 0)
