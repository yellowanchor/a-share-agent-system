# -*- coding: utf-8 -*-
"""paper.py — 模拟交易账户（尽量贴近 A 股真实规则）

设计要点（比"记一笔买入"更拟真的地方）：
1. **账户制**：一个虚拟账户（默认 10 万现金），买入扣现金、卖出回现金，
   总资产 = 现金 + 持仓市值，收益率按总资产算，而不是把每笔买入单独加总；
2. **真实费用**：佣金（万 2.5，最低 5 元）、印花税（卖出 0.05%）、过户费（0.001%），
   买卖都计入成本，平仓盈亏是**扣费后的净额**；
3. **滑点**：买入成交价 = 报价 ×（1+0.1%），卖出成交价 = 报价 ×（1−0.1%）；
4. **T+1**：当天买入的股票当天不能卖（``can_sell`` 为 False）；
5. **整手**：股数按 100 股取整，不足 100 股按 100 股（与真实下单一致）；
6. **持有期限**：``due_date = buy_date + holding_days``（自然日），到期只提示不自动平仓。

落盘：``data/paper/positions.json``
```json
{"version": 2,
 "account": {"cash": 100000.0, "initial": 100000.0},
 "positions": [...], "trades": [...]}
```
老版本（version 1，只有 positions）会被自动迁移。
"""

from __future__ import annotations

import json
import uuid
from datetime import date as _date
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from src.app import boards

ROOT = Path(__file__).resolve().parents[2]
PAPER_DIR = ROOT / "data" / "paper"
POS_FILE = PAPER_DIR / "positions.json"

DEFAULT_AMOUNT = 10000.0     # 默认每笔模拟买入 1 万元
INITIAL_CAPITAL = 100000.0   # 初始虚拟资金
MIN_CAPITAL = 100.0          # 初始资金下限（支持几百元的小额账户）

# ---- 交易成本（A 股常见口径，可按需改）----
COMMISSION_RATE = 0.00025    # 佣金 万 2.5
MIN_COMMISSION = 5.0         # 单笔最低 5 元
STAMP_TAX_RATE = 0.0005      # 印花税，仅卖出
TRANSFER_FEE_RATE = 0.00001  # 过户费 0.001%
SLIPPAGE = 0.001             # 滑点 0.1%


# ---------------------------------------------------------------- 存取
def _default_state() -> dict[str, Any]:
    return {
        "version": 2,
        "account": {"cash": INITIAL_CAPITAL, "initial": INITIAL_CAPITAL},
        "positions": [],
        "trades": [],
    }


def load_state() -> dict[str, Any]:
    """读账户状态；兼容老版本（只有 positions 的 v1 文件）。"""
    if not POS_FILE.exists():
        return _default_state()
    try:
        with open(POS_FILE, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return _default_state()

    if isinstance(raw, list):                       # 极老格式：裸列表
        return {**_default_state(), "positions": raw}
    state = {**_default_state(), **raw}
    state.setdefault("account", {"cash": INITIAL_CAPITAL, "initial": INITIAL_CAPITAL})
    state.setdefault("positions", [])
    state.setdefault("trades", [])
    state["version"] = 2
    return state


def save_state(state: dict[str, Any]) -> None:
    PAPER_DIR.mkdir(parents=True, exist_ok=True)
    tmp = POS_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    tmp.replace(POS_FILE)


def load_positions() -> list[dict[str, Any]]:
    return list(load_state().get("positions") or [])


def save_positions(rows: list[dict[str, Any]]) -> None:
    state = load_state()
    state["positions"] = rows
    save_state(state)


def load_trades() -> list[dict[str, Any]]:
    return list(load_state().get("trades") or [])


def cash_available() -> float:
    return float(load_state()["account"]["cash"])


def is_fresh() -> bool:
    """账户还没动过：既没有持仓，也没有成交流水。"""
    state = load_state()
    return not state.get("positions") and not state.get("trades")


def set_capital(amount: float, *, reset: bool = False) -> dict[str, float]:
    """设定账户初始资金。

    - 空账户（无持仓、无流水）：直接把 cash 与 initial 都设成 amount；
    - reset=True：清空持仓与流水后重开账户，cash = initial = amount；
    - 有持仓/流水且没勾重置：抛 ValueError，避免把已有盈亏算错。
    """
    amount = round(float(amount), 2)
    if amount < MIN_CAPITAL:
        raise ValueError("初始资金至少 1,000 元")
    if amount > 100_000_000:
        raise ValueError("初始资金上限 1 亿元")

    state = load_state()
    has_history = bool(state.get("positions") or state.get("trades"))
    if has_history and not reset:
        raise ValueError("账户已有持仓或流水；要改初始资金请勾选「重置账户」")
    state["positions"] = []
    state["trades"] = []
    state["account"] = {"cash": amount, "initial": amount}
    save_state(state)
    return {"cash": amount, "initial": amount}


# ---------------------------------------------------------------- 费用
def commission(gross: float) -> float:
    return round(max(MIN_COMMISSION, gross * COMMISSION_RATE), 2)


def transfer_fee(gross: float) -> float:
    return round(gross * TRANSFER_FEE_RATE, 2)


def stamp_tax(gross: float) -> float:
    return round(gross * STAMP_TAX_RATE, 2)


def buy_cost(gross: float) -> float:
    """买入需要支付的交易成本（佣金 + 过户费）。"""
    return round(commission(gross) + transfer_fee(gross), 2)


def sell_cost(gross: float) -> float:
    """卖出需要支付的交易成本（佣金 + 印花税 + 过户费）。"""
    return round(commission(gross) + stamp_tax(gross) + transfer_fee(gross), 2)


# ---------------------------------------------------------------- 买入
def add_position(
    code: str,
    name: str,
    price: float,
    *,
    amount: float = DEFAULT_AMOUNT,
    holding_days: int = 10,
    buy_date: str | None = None,
    thesis: str = "",
    source: str = "看板手动",
    shares: int | None = None,
) -> dict[str, Any]:
    """模拟买入。报错时抛 ValueError，调用方把 message 显示给用户。"""
    if price is None or float(price) <= 0:
        raise ValueError("买入价必须大于 0")

    six = boards.six(code)
    day = buy_date or _date.today().isoformat()
    quote = float(price)
    fill = round(quote * (1 + SLIPPAGE), 2)          # 含滑点的成交价

    state = load_state()
    cash = float(state["account"]["cash"])

    if shares:
        qty = int(shares) // 100 * 100
        if qty < 100:
            qty = 100
    else:
        # 按金额下单：买得起多少手就买多少手（向下取整到 100 股）
        budget = min(float(amount), cash)
        qty = int(budget / fill / 100.0) * 100

    gross = round(qty * fill, 2)
    fee = buy_cost(gross)
    total = round(gross + fee, 2)

    if qty < 100:
        raise ValueError(
            f"{fill:.2f} 元/股，1 手（100 股）约需 {fill * 100:,.0f} 元，"
            f"超出可买金额 {min(float(amount), cash):,.0f} 元"
        )
    if total > cash + 1e-6:
        raise ValueError(
            f"可用现金不足：需要 {total:,.2f} 元（含费用 {fee:,.2f}），当前只有 {cash:,.2f} 元"
        )

    pos = {
        "id": uuid.uuid4().hex[:10],
        "code": six,
        "name": name or six,
        "board": boards.board_of(six),
        "buy_date": day,
        "buy_price": fill,               # 成交价（含滑点）
        "quote_price": round(quote, 2),   # 下单时看到的报价
        "shares": qty,
        "amount": gross,                  # 成交金额（不含费用）
        "fee": fee,                       # 买入费用
        "cost": total,                    # 总成本 = 成交金额 + 费用
        "holding_days": int(holding_days),
        "due_date": (_date.fromisoformat(day)
                     + timedelta(days=int(holding_days))).isoformat(),
        "thesis": thesis,
        "source": source,
        "status": "open",
        "exit_date": None,
        "exit_price": None,
        "exit_reason": None,
        "net_pnl": None,
    }
    state["account"]["cash"] = round(cash - total, 2)
    state["positions"].append(pos)
    state["trades"].append({
        "time": datetime.now().isoformat(timespec="seconds"),
        "side": "buy", "code": six, "name": pos["name"],
        "price": fill, "shares": qty, "gross": gross, "fee": fee,
        "cash_after": state["account"]["cash"],
    })
    save_state(state)
    return pos


# ---------------------------------------------------------------- 卖出
def close_position(
    pos_id: str,
    price: float,
    *,
    day: str | None = None,
    reason: str = "手动平仓",
) -> dict[str, Any]:
    """平仓（全部卖出）。返回成交明细；卖不掉时抛 ValueError。"""
    if price is None or float(price) <= 0:
        raise ValueError("卖出价必须大于 0")

    state = load_state()
    day = day or _date.today().isoformat()
    hit = None
    for r in state["positions"]:
        if r.get("id") == pos_id and r.get("status") == "open":
            hit = r
            break
    if hit is None:
        raise ValueError("找不到这笔持仓")

    if str(day) <= str(hit.get("buy_date")):
        raise ValueError(
            f"T+1 限制：{hit.get('buy_date')} 买入，最早 {hit.get('buy_date')} 的下一交易日才能卖出"
        )

    qty = int(hit.get("shares") or 0)
    fill = round(float(price) * (1 - SLIPPAGE), 2)
    gross = round(qty * fill, 2)
    fee = sell_cost(gross)
    net_in = round(gross - fee, 2)
    cost = float(hit.get("cost") or hit.get("amount") or 0)

    hit.update({
        "status": "closed",
        "exit_price": fill,
        "exit_quote": round(float(price), 2),
        "exit_date": day,
        "exit_reason": reason,
        "exit_gross": gross,
        "exit_fee": fee,
        "net_pnl": round(net_in - cost, 2),
    })
    state["account"]["cash"] = round(float(state["account"]["cash"]) + net_in, 2)
    state["trades"].append({
        "time": datetime.now().isoformat(timespec="seconds"),
        "side": "sell", "code": hit["code"], "name": hit["name"],
        "price": fill, "shares": qty, "gross": gross, "fee": fee,
        "cash_after": state["account"]["cash"],
    })
    save_state(state)
    return hit


# ---------------------------------------------------------------- 统计
def pnl(row: dict[str, Any], cur_price: float | None) -> tuple[float, float]:
    """(净盈亏金额, 净盈亏率)；已扣买卖两端费用。"""
    cost = float(row.get("cost") or row.get("amount") or 0)
    if row.get("status") == "closed":
        gain = float(row.get("net_pnl") or 0)
        return gain, (gain / cost if cost else 0.0)

    if cur_price is None or cost <= 0:
        return 0.0, 0.0
    qty = int(row.get("shares") or 0)
    gross = round(qty * float(cur_price), 2)
    net_out = gross - sell_cost(gross)     # 假设此刻卖出，扣掉卖出费用
    gain = round(net_out - cost, 2)
    return gain, gain / cost


def enrich(
    rows: list[dict[str, Any]],
    price_map: dict[str, tuple[float | None, str]],
    *,
    today: str | None = None,
) -> list[dict[str, Any]]:
    """补现价、市值、净盈亏、剩余天数、是否可卖（T+1）等派生字段。"""
    day = today or _date.today().isoformat()
    today_d = _date.fromisoformat(day)
    out = []
    for r in rows:
        cur, cur_date = price_map.get(r["code"], (None, ""))
        gain, pct = pnl(r, cur)
        due = _date.fromisoformat(r["due_date"]) if r.get("due_date") else today_d
        days_left = (due - today_d).days
        qty = int(r.get("shares") or 0)
        out.append({
            **r,
            "cur_price": cur,
            "cur_date": cur_date,
            "market_value": round(qty * cur, 2) if cur else None,
            "pnl": gain,
            "pnl_pct": pct,
            "days_left": days_left,
            "expired": days_left < 0 and r.get("status") == "open",
            "can_sell": str(day) > str(r.get("buy_date")),
        })
    return out


def summarize(enriched: list[dict[str, Any]]) -> dict[str, Any]:
    """账户口径汇总：总资产 = 现金 + 持仓市值。"""
    state = load_state()
    cash = float(state["account"]["cash"])
    initial = float(state["account"].get("initial") or INITIAL_CAPITAL)

    open_rows = [r for r in enriched if r.get("status") == "open"]
    closed = [r for r in enriched if r.get("status") == "closed"]

    market_value = sum(float(r.get("market_value") or 0) for r in open_rows)
    cost_open = sum(float(r.get("cost") or 0) for r in open_rows)
    floating = sum(float(r.get("pnl") or 0) for r in open_rows)
    realized = sum(float(r.get("net_pnl") or 0) for r in closed)
    wins = sum(1 for r in closed if float(r.get("net_pnl") or 0) > 0)

    total = round(cash + market_value, 2)
    return {
        "n_open": len(open_rows),
        "n_closed": len(closed),
        "cash": round(cash, 2),
        "initial": initial,
        "market_value": round(market_value, 2),
        "cost_open": round(cost_open, 2),
        "total_assets": total,
        "total_return": (total / initial - 1) if initial else 0.0,
        "floating": round(floating, 2),
        "floating_pct": (floating / cost_open) if cost_open else 0.0,
        "realized": round(realized, 2),
        "win_rate": (wins / len(closed)) if closed else 0.0,
        "expired": sum(1 for r in open_rows if r.get("expired")),
    }

