# -*- coding: utf-8 -*-
"""每日建议的资金自适应：1 手成本与可买手数换算。"""

from __future__ import annotations

import pytest

from src.app.pages.daily_picks import lot_cost, plan_buy


def test_lot_cost_rounds_to_hand():
    assert lot_cost(4.47) == pytest.approx(447.0)
    assert lot_cost(12.345) == pytest.approx(1234.5)
    assert lot_cost(None) is None
    assert lot_cost(0) is None


def test_plan_buy_respects_position_pct():
    # 现金 10 万、仓位 10% → 预算 1 万 → 4.47 元可买 22 手
    plan = plan_buy(4.47, 10, 100000)
    assert plan["lots"] == 22
    assert plan["shares"] == 2200
    assert plan["amount"] == pytest.approx(9834.0)
    assert plan["spend_pct"] < 10


def test_plan_buy_falls_back_to_one_lot():
    # 预算不足一手但现金够一手 → 给一手
    plan = plan_buy(24.90, 1, 2500)
    assert plan["lots"] == 1
    assert plan["amount"] == pytest.approx(2490.0)


def test_plan_buy_rejects_when_cash_below_one_lot():
    plan = plan_buy(1486.0, 20, 600)
    assert plan["lots"] == 0
    assert plan["amount"] == 0
    assert plan["spend_pct"] == 0


def test_plan_buy_without_position_pct_uses_twenty_percent():
    plan = plan_buy(10.0, None, 10000)   # 预算 2000 → 2 手
    assert plan["lots"] == 2
    assert plan["amount"] == pytest.approx(2000.0)


def test_plan_buy_tiny_cash():
    plan = plan_buy(4.47, 100, 600)      # 预算 600 → 1 手 447
    assert plan["lots"] == 1
    assert plan["amount"] == pytest.approx(447.0)
    assert plan["spend_pct"] == pytest.approx(74.5)
