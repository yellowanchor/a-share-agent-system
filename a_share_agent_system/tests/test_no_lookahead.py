# -*- coding: utf-8 -*-
"""test_no_lookahead.py —— 未来函数防回归测试

为什么单独一个文件
------------------
回测/复盘最致命的错误是"用了当时还看不到的数据"：业绩会虚高，答辩时被追问一句
"这个 as_of 当时真的知道这条新闻吗"就崩。所以每个按 as_of 取数的入口都要有守卫：

1. 情感日聚合     src/app/data.py::load_sentiment_daily(code, as_of)
2. 财务 PIT 快照  MarketStore.latest_financial(code, as_of)
3. 新闻结构化兜底 LocalVectorStore.recent_by_stock(code, limit, end)
4. 新闻语义检索   Analyst 传给检索器的时间上界 _end_ts(as_of)

全部用**临时数据**构造（不依赖库里的真实数据），因此可以在任何机器上离线跑。

运行:
    pytest tests/test_no_lookahead.py -v
"""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd
import pytest

import src.app.data as app_data
from src.agents.analyst import _end_ts
from src.data.storage.market_store import MarketStore
from src.models.rag.local_store import LocalVectorStore


# ------------------------------------------------------------------ 1. 情感
def test_sentiment_daily_stops_at_as_of(tmp_path, monkeypatch):
    """as_of 之后的新闻不得进入情感日聚合。"""
    df = pd.DataFrame(
        {
            "stock_code": ["600519", "600519", "600519", "600519"],
            "publish_time": [
                "2026-06-29 10:00:00",  # 可见
                "2026-06-30 17:55:00",  # 可见（当日 23:59:59 之前）
                "2026-07-01 00:00:00",  # 不可见（次日 0 点）
                "2026-07-02 09:30:00",  # 不可见
            ],
            "label": ["positive", "negative", "positive", "positive"],
        }
    )
    sent_path = tmp_path / "sentiment_labeled.parquet"
    df.to_parquet(sent_path, index=False)
    monkeypatch.setattr(app_data, "SENT_PARQUET", sent_path)

    got = app_data.load_sentiment_daily("600519", as_of="2026-06-30")

    days = [str(d) for d in got["date"]]
    assert days == ["2026-06-29", "2026-06-30"], f"越界日期泄漏: {days}"
    assert int(got["n"].sum()) == 2  # 只统计可见的两条


def test_sentiment_daily_without_as_of_keeps_all(tmp_path, monkeypatch):
    """不传 as_of 时保持原行为（全部历史），避免改坏页面默认表现。"""
    df = pd.DataFrame(
        {
            "stock_code": ["600519", "600519"],
            "publish_time": ["2026-06-30 10:00:00", "2026-07-01 00:00:00"],
            "label": ["positive", "negative"],
        }
    )
    sent_path = tmp_path / "sentiment_labeled.parquet"
    df.to_parquet(sent_path, index=False)
    monkeypatch.setattr(app_data, "SENT_PARQUET", sent_path)

    got = app_data.load_sentiment_daily("600519")
    assert len(got) == 2


# ------------------------------------------------------------------ 2. 财务
def _make_store(tmp_path) -> MarketStore:
    """建一个只含 financials_yjbb 的最小 DuckDB。"""
    db = tmp_path / "market.duckdb"
    con = duckdb.connect(str(db))
    con.execute(
        "CREATE TABLE financials_yjbb ("
        "code VARCHAR, name VARCHAR, report_date DATE, pub_date DATE, eps DOUBLE)"
    )
    con.execute(
        "INSERT INTO financials_yjbb VALUES "
        "('600519','贵州茅台', DATE '2026-03-31', DATE '2026-04-25', 30.00),"
        "('600519','贵州茅台', DATE '2026-06-30', DATE '2026-08-15', 35.57)"
    )
    con.close()
    return MarketStore(db_path=db)


def test_latest_financial_respects_pub_date(tmp_path):
    """as_of=2026-07-01 时，8 月 15 日才披露的半年报必须看不见。"""
    store = _make_store(tmp_path)

    early = store.latest_financial("600519", as_of="2026-07-01")
    assert len(early) == 1
    assert str(early.iloc[0]["report_date"])[:10] == "2026-03-31"

    late = store.latest_financial("600519", as_of="2026-11-30")
    assert str(late.iloc[0]["report_date"])[:10] == "2026-06-30"


# ------------------------------------------------------- 3. 新闻结构化兜底
def _make_vector_store(tmp_path) -> LocalVectorStore:
    """手写一个 3 条新闻的索引，避免调用 bge-m3 编码。"""
    meta = pd.DataFrame(
        {
            "id": ["n1", "n2", "n3"],
            "title": ["t1", "t2", "t3"],
            "text": ["x", "x", "x"],
            "source": ["测试"] * 3,
            "stock_code": ["600519"] * 3,
            "publish_time": [
                "2026-06-28 10:00:00",
                "2026-07-01 10:00:00",  # 越界
                "2026-06-30 17:00:00",
            ],
        }
    )
    np.save(tmp_path / "embeddings.npy", np.zeros((3, 4), dtype=np.float32))
    meta.to_parquet(tmp_path / "meta.parquet", index=False)
    return LocalVectorStore(tmp_path, use_gpu=False)


def test_recent_by_stock_respects_end(tmp_path):
    """兜底路径按 publish_time 倒序取最近 N 条时，必须遵守 end 上界。"""
    store = _make_vector_store(tmp_path)

    got = store.recent_by_stock("600519", limit=10, end="2026-06-30 23:59:59")
    assert [r["id"] for r in got] == ["n3", "n1"], f"越界新闻泄漏: {[r['id'] for r in got]}"

    # 不传 end 时保持原行为（三条都能看到）
    assert len(store.recent_by_stock("600519", limit=10)) == 3


# ------------------------------------------------------- 4. 语义检索时间上界
def test_end_ts_covers_whole_day():
    """as_of 是"日"，传检索器时必须补成当日 23:59:59，否则漏掉当天晚间新闻。"""
    assert _end_ts("2026-06-30") == "2026-06-30 23:59:59"
    assert _end_ts("2026-06-30 08:00:00") == "2026-06-30 23:59:59"
    assert _end_ts(None) is None


if __name__ == "__main__":  # 无 pytest 时的顺序执行入口
    raise SystemExit(pytest.main([__file__, "-v"]))
