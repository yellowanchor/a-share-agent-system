"""L2 现场新闻抓取（news_live）的离线测试。

覆盖两条必须守住的规则：
  1. 历史 as_of 一律返回空 —— 回测绝不能看到"现在"的新闻（未来函数）；
  2. 同一天重复调用走 6 小时缓存，不再打网络。
不联网：所有网络函数都用 monkeypatch 替换。
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from src.data.collect import news_live


def test_past_as_of_returns_empty(monkeypatch):
    """as_of 早于今天 → 空列表，且完全不触发网络请求。"""
    called = []

    def _boom(*a, **kw):
        called.append(1)
        raise AssertionError("历史 as_of 不应触发抓取")

    monkeypatch.setattr(news_live, "fetch_news", _boom)
    past = (date.today() - timedelta(days=3)).isoformat()
    assert news_live.live_news("600519", as_of=past) == []
    assert called == []


def test_today_hits_cache_after_first_fetch(monkeypatch, tmp_path):
    """今天：首次抓取走网络，第二次命中缓存。"""
    monkeypatch.setattr(news_live, "CACHE_DIR", tmp_path, raising=False)

    calls = []

    def _fake_fetch(symbol6, max_pages=1, as_of=None, **kw):
        calls.append(symbol6)
        return [{
            "title": "测试新闻",
            "text": "公司发布公告",
            "source": "test",
            "stock_code": symbol6,
            "publish_time": "2026-11-23 09:30:00",
            "url": "https://example.com/a?utm_source=x",
        }]

    monkeypatch.setattr(news_live, "fetch_news", _fake_fetch)

    first = news_live.live_news("600519", as_of=None)
    second = news_live.live_news("600519", as_of=None)

    assert len(calls) == 1, "第二次应命中缓存"
    assert first and second and first[0]["id"] == second[0]["id"]
    assert first[0]["live"] is True
    cached = list(tmp_path.glob("600519_*.json"))
    assert cached, "缓存文件应落盘"
    assert json.loads(cached[0].read_text(encoding="utf-8"))["ts"] > 0


def test_dedupe_by_normalized_url(monkeypatch, tmp_path):
    """同一文章不同跟踪参数 → 归一化后去重成一条。"""
    monkeypatch.setattr(news_live, "CACHE_DIR", tmp_path, raising=False)

    def _fake_fetch(symbol6, max_pages=1, as_of=None, **kw):
        return [
            {"title": "A", "text": "x", "source": "s", "stock_code": symbol6,
             "publish_time": "2026-11-23 10:00:00", "url": "https://example.com/n?utm_source=a"},
            {"title": "A", "text": "x", "source": "s", "stock_code": symbol6,
             "publish_time": "2026-11-23 10:00:00", "url": "https://example.com/n"},
        ]

    monkeypatch.setattr(news_live, "fetch_news", _fake_fetch)
    got = news_live.live_news("000001", as_of=None, force=True)
    assert len(got) == 1, f"应去重为 1 条，实际 {len(got)}"
