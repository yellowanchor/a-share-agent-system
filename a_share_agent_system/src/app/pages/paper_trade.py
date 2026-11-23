# -*- coding: utf-8 -*-
"""模拟持仓页：一个虚拟账户，按 A 股规则记买记卖。

和"记一笔买入"的区别：
- 有**现金账户**（默认 10 万），买入扣现金、卖出回款，总资产 = 现金 + 持仓市值；
- 买卖都计**佣金 / 印花税 / 过户费**，平仓盈亏是净额；
- 买入价含 0.1% 滑点，卖出同理；
- **T+1**：当天买的当天不能卖；
- 底部有**成交流水**，可以回看每一笔钱怎么走的。
"""

from __future__ import annotations

import streamlit as st

from src.app import boards, data, layout, paper, views


def _money(v: float | None) -> str:
    return "—" if v is None else f"{v:,.2f}"


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v * 100:+.2f}%"


def _cls(v: float | None) -> str:
    v = float(v or 0)
    return "pos" if v > 0 else ("neg" if v < 0 else "")


def _row_html(r: dict) -> str:
    gain = float(r.get("pnl") or 0)
    board = r.get("board") or boards.board_of(r["code"])
    if r.get("expired"):
        tag = '<span class="pt-due">已到期</span>'
    elif not r.get("can_sell"):
        tag = '<span class="pt-t1">T+1 明日可卖</span>'
    else:
        tag = ""
    return (
        '<div class="ptrow">'
        f'<div class="pt-name">{views.esc(r.get("name") or "")}'
        f'<span class="pt-code">{r["code"]}</span></div>'
        f'<div class="pt-board">{views.pill(board, boards.board_cls(r["code"]))}</div>'
        f'<div class="pt-cell"><span class="pk">成本价</span>'
        f'<span class="pv">{_money(r.get("buy_price"))}</span></div>'
        f'<div class="pt-cell"><span class="pk">现价</span>'
        f'<span class="pv">{_money(r.get("cur_price"))}</span></div>'
        f'<div class="pt-cell"><span class="pk">市值</span>'
        f'<span class="pv">{_money(r.get("market_value"))}</span></div>'
        f'<div class="pt-cell"><span class="pk">净盈亏</span>'
        f'<span class="pv {_cls(gain)}">{gain:+,.2f}</span></div>'
        f'<div class="pt-pct {_cls(gain)}">{_pct(float(r.get("pnl_pct") or 0))}</div>'
        f'<div class="pt-days"><span class="pk">剩余</span>'
        f'<span class="pv">{r.get("days_left")} 天</span>{tag}</div>'
        "</div>"
    )


def _metrics(stat: dict) -> None:
    layout.md(
        views.metrics_row([
            (f"{stat['total_assets']:,.0f}", "总资产"),
            (f"{stat['cash']:,.0f}", "可用现金"),
            (f"{stat['market_value']:,.0f}", "持仓市值"),
            (f"{stat['floating']:+,.0f}", "浮动盈亏"),
            (f"{stat['realized']:+,.0f}", "已实现"),
            (f"{stat['total_return'] * 100:+.2f}<span class='unit'>%</span>", "总收益率"),
        ])
    )


def render() -> None:
    if layout.busy_guard():
        return

    rows = paper.load_positions()
    codes = tuple(sorted({str(r.get("code")) for r in rows if r.get("code")}))
    pmap = data.price_map(codes) if codes else {}
    enriched = paper.enrich(rows, pmap)
    stat = paper.summarize(enriched)

    layout.page_head(
        "模拟持仓",
        f"虚拟账户 {stat['initial']:,.0f} 元起，按真实费率与 T+1 规则成交；"
        "用来验证\"我的判断能不能赚钱\"。",
    )
    _metrics(stat)

    # ------------------------------------------------------------ 账户设置
    fresh = paper.is_fresh()
    with st.expander("⚙ 账户设置（初始资金）", expanded=fresh):
        s1, s2 = st.columns([1.5, 1.5], vertical_alignment="bottom")
        with s1:
            cap = st.number_input(
                "初始资金（元）", min_value=float(paper.MIN_CAPITAL), max_value=100_000_000.0,
                value=float(stat["initial"]), step=10000.0, format="%.2f",
                key="paper_capital",
            )
        with s2:
            reset = st.checkbox(
                "重置账户（清空持仓与流水）", value=False,
                key="paper_reset", disabled=fresh,
                help="没有持仓和流水时可以直接改；已经交易过就需要勾选重置。",
            )
        if st.button("保存初始资金", key="paper_save_cap"):
            try:
                paper.set_capital(cap, reset=reset or fresh)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.success(f"已设定：初始资金 {cap:,.2f} 元，可用现金同步为 {cap:,.2f} 元")
                st.rerun()
        if not fresh:
            st.caption("当前账户已有交易记录；改初始资金会同时清空持仓与流水（相当于重开账户）。")

    if stat["expired"]:
        st.warning(f"有 {stat['expired']} 笔持仓已超过预设期限，建议平仓结算后再看结论。")

    # ---------------------------------------------------------------- 买入
    with st.expander("＋ 模拟买入", expanded=not rows):
        mode = st.radio("下单方式", ["按金额", "按股数"], horizontal=True,
                        label_visibility="collapsed", key="paper_mode")
        with st.form("paper_buy", clear_on_submit=True):
            c1, c2, c3, c4 = st.columns([1.2, 1.2, 1.2, 1.2],
                                        vertical_alignment="bottom")
            with c1:
                code = st.text_input("股票代码", placeholder="如 600519 / 300750")
            with c2:
                price_in = st.text_input("委托价（留空=最新收盘）", value="")
            with c3:
                if mode == "按金额":
                    amount = st.number_input(
                        "金额（元）", min_value=1000.0,
                        value=min(paper.DEFAULT_AMOUNT, max(1000.0, stat["cash"])),
                        step=1000.0,
                    )
                    qty_in = 0
                else:
                    qty_in = st.number_input("股数（100 的整数倍）", min_value=100,
                                             value=100, step=100)
                    amount = 0.0
            with c4:
                days = st.number_input("持有期限（天）", min_value=1, max_value=250,
                                       value=10, step=1)
            thesis = st.text_input("买入理由（一句话，复盘时看）", value="")
            ok = st.form_submit_button("买入", type="primary")

        st.caption(
            f"佣金万 2.5（最低 {paper.MIN_COMMISSION:.0f} 元）· 印花税 0.05%（仅卖出）"
            f"· 过户费 0.001% · 滑点 {paper.SLIPPAGE * 100:.1f}% · 整手 100 股"
        )

        if ok:
            six = boards.six(code)
            if len(six) != 6:
                st.error("代码不对，需要 6 位数字。")
            else:
                pm = data.price_map((six,))
                cur, cur_date = pm.get(six, (None, ""))
                px = float(price_in) if price_in.strip() else (cur or 0)
                if not px:
                    st.error("取不到最新收盘价，请手动填委托价。")
                else:
                    try:
                        pos = paper.add_position(
                            six, data.name_of(six), px,
                            amount=float(amount or 0),
                            shares=int(qty_in) if qty_in else None,
                            holding_days=int(days),
                            buy_date=cur_date or None,
                            thesis=thesis, source="模拟持仓页",
                        )
                    except ValueError as exc:
                        st.error(str(exc))
                    else:
                        st.success(
                            f"已成交：{pos['name']} {pos['shares']} 股 @ {pos['buy_price']:.2f}"
                            f"（含费 {pos['fee']:.2f} 元），期限 {int(days)} 天"
                        )
                        st.rerun()

    # ---------------------------------------------------------------- 持仓
    open_rows = [r for r in enriched if r.get("status") == "open"]
    layout.page_head(
        "持仓中",
        f"{len(open_rows)} 笔 · 市值按最新收盘价，净盈亏已扣卖出费用",
    )
    if not open_rows:
        st.info("还没有模拟持仓。可以在「每日建议」页点「模拟买入」，或在上面的表单里记一笔。")
    else:
        for r in open_rows:
            left, right = st.columns([6.2, 1], vertical_alignment="center")
            with left:
                layout.md(_row_html(r))
            with right:
                if st.button("卖出", key=f"close_{r['id']}", use_container_width=True,
                             disabled=not r.get("can_sell")):
                    px = r.get("cur_price")
                    if not px:
                        st.error("取不到现价，无法卖出。")
                    else:
                        try:
                            paper.close_position(
                                r["id"], px,
                                reason="到期平仓" if r.get("expired") else "手动卖出",
                            )
                        except ValueError as exc:
                            st.error(str(exc))
                        else:
                            st.rerun()

    # ---------------------------------------------------------------- 已平仓
    closed = [r for r in enriched if r.get("status") == "closed"]
    if closed:
        layout.page_head("已平仓",
                         f"{len(closed)} 笔 · 胜率 {stat['win_rate'] * 100:.0f}%"
                         " · 盈亏为扣费后净额")
        rows_html = "".join(
            f'<div class="prow">'
            f'<span class="pr-name">{views.esc(r.get("name") or "")}'
            f'<span class="pr-code">{r["code"]}</span></span>'
            f'<span class="pr-score">{_money(r.get("buy_price"))} → '
            f'{_money(r.get("exit_price"))}</span>'
            f'<span class="pr-reason {_cls(float(r.get("net_pnl") or 0))}">'
            f'{float(r.get("net_pnl") or 0):+,.2f}（{_pct(float(r.get("pnl_pct") or 0))}）'
            f' · {r.get("exit_date")} · {r.get("exit_reason") or ""}</span></div>'
            for r in sorted(closed, key=lambda x: str(x.get("exit_date") or ""),
                            reverse=True)
        )
        layout.md(rows_html)

    # ---------------------------------------------------------------- 流水
    trades = paper.load_trades()
    if trades:
        layout.page_head("成交流水", f"共 {len(trades)} 笔（含费用与成交后现金）")
        body = "".join(
            f'<div class="prow">'
            f'<span class="pr-name">{"买入" if t.get("side") == "buy" else "卖出"}'
            f'　{views.esc(t.get("name") or "")}'
            f'<span class="pr-code">{t.get("code")}</span></span>'
            f'<span class="pr-score">{t.get("shares")} 股 @ {_money(t.get("price"))}'
            f'　费 {_money(t.get("fee"))}</span>'
            f'<span class="pr-reason">{str(t.get("time") or "")[:16].replace("T", " ")}'
            f' · 余额 {_money(t.get("cash_after"))}</span></div>'
            for t in sorted(trades, key=lambda x: str(x.get("time") or ""),
                            reverse=True)[:30]
        )
        layout.md(body)

    layout.md(
        '<p class="disclaimer">模拟盘只记录判断、不涉及真实资金；已计佣金 / 印花税 / '
        "过户费与 0.1% 滑点，但没有模拟涨跌停无法成交、停牌、分红送转等情况。</p>"
    )
