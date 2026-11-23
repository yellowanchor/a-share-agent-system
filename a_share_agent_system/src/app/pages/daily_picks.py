# -*- coding: utf-8 -*-
"""每日建议页：读 ``data/signals/<date>.json``，渲染"今日建议入手"卡片。

卡片信息 = 综合分（0–100，四因子加权）+ 结论（buy/watch/reject）
+ 真实价格区间（买入 / 止损 / 止盈 / 现价）+ 仓位 + 一句话理由 + 新闻闸门结论。
被闸门否决或分数不够的进"今日否决区"，不留白。

**资金约束**：卡片会按模拟账户的「可用现金」算出每只标的 1 手要多少钱
（A 股最小 100 股），买不起的单独归入「资金不足区」，能买的按建议仓位
换算成股数与金额，点击「模拟买入」直接按这个金额下单。
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from src.app import boards, data, layout, paper, views
from src.app.pages.data_ops import PY, _run_jobs

_VERDICT_CN = {"buy": "建议买入", "watch": "观察", "reject": "不建议"}
_VERDICT_CLS = {"buy": "good", "watch": "warn", "reject": "bad"}
_LOT = 100  # A 股一手 100 股


def _money(v: Any) -> str:
    return data.fmt_num(v, 2)


def _pct(v: Any) -> str:
    return "—" if v is None else f"{float(v):.1f}%"


def _pvcls(v: Any) -> str:
    try:
        f = float(v or 0)
    except (TypeError, ValueError):
        return ""
    return "pos" if f > 0 else ("neg" if f < 0 else "")


# ------------------------------------------------------------------ 资金换算
def lot_cost(close: Any) -> float | None:
    """1 手（100 股）需要多少钱；价格缺失返回 None。"""
    try:
        price = float(close)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    return round(price * _LOT, 2)


def plan_buy(close: Any, position_pct: Any, cash: float) -> dict[str, Any]:
    """按「可用现金 × 建议仓位」算这一笔能买多少股、花多少钱。

    返回 ``{"lots": n, "shares": n*100, "amount": 金额, "hand": 1手成本}``；
    买不起 1 手时 ``lots=0``。
    """
    hand = lot_cost(close)
    if hand is None or cash <= 0:
        return {"lots": 0, "shares": 0, "amount": 0.0, "hand": hand, "spend_pct": 0.0}
    try:
        ratio = max(0.0, min(1.0, float(position_pct or 0) / 100.0))
    except (TypeError, ValueError):
        ratio = 0.0
    budget = cash * ratio if ratio > 0 else cash * 0.2   # 仓位缺失时按 2 成
    budget = min(budget, cash)
    lots = int(budget // hand)
    if lots <= 0 and hand <= cash:      # 预算不够一手但现金够，就给一手
        lots = 1
    lots = max(0, lots)
    amount = round(lots * hand, 2)
    return {
        "lots": lots,
        "shares": lots * _LOT,
        "amount": amount,
        "hand": hand,
        "spend_pct": round(amount / cash * 100, 1) if cash > 0 else 0.0,
    }


def _card_html(it: dict[str, Any], cash: float) -> str:
    code = str(it.get("code") or "")[-6:]
    name = it.get("name") or code
    score = it.get("score")
    score_txt = "—" if score is None else f"{float(score):.0f}"
    pct = 0 if score is None else max(0, min(100, float(score)))
    verdict = str(it.get("verdict") or "watch")
    v_cn = _VERDICT_CN.get(verdict, verdict)
    v_cls = _VERDICT_CLS.get(verdict, "warn")

    gate = it.get("gate") or {}
    gate_v = str(gate.get("verdict") or "pass")
    gate_cn = {"pass": "闸门通过", "caution": "闸门提示", "veto": "闸门否决"}.get(gate_v, gate_v)
    gate_cls = {"pass": "good", "caution": "warn", "veto": "bad"}.get(gate_v, "warn")
    ev_n = len(it.get("evidence") or [])
    live_n = sum(1 for e in (it.get("evidence") or []) if e.get("retrieval") == "live")

    pos = it.get("position_pct")
    close = it.get("close")
    board = boards.board_of(code)

    plan = plan_buy(close, pos, cash)
    if plan["lots"]:
        cls = "good" if plan["spend_pct"] <= 40 else ("warn" if plan["spend_pct"] <= 80 else "bad")
        budget_pill = views.pill(
            f"可买 {plan['lots']} 手 ≈ {_money(plan['amount'])} 元 · 占现金 {plan['spend_pct']:.0f}%",
            cls,
        )
    else:
        budget_pill = views.pill(
            f"1 手需 {_money(plan['hand'])} 元（现金不足）", "bad")

    return (
        f'<div class="pcard">'
        f'<div class="pcard-head">'
        f'<div><div class="pcard-name">{views.esc(name)}</div>'
        f'<div class="pcard-code">{views.esc(code)} · 现价 {_money(close)} 元</div></div>'
        f'<div class="score-ring" style="--pct:{pct}"><span>{score_txt}</span></div>'
        f"</div>"
        f'<div class="pcard-pills">'
        f'{views.pill(v_cn, v_cls)}'
        f'{views.pill(board, boards.board_cls(code))}'
        f'{views.pill(f"仓位 {_pct(pos)}", "neu")}'
        f'{views.pill(gate_cn, gate_cls)}'
        f'{views.pill(f"证据 {ev_n}（现场 {live_n}）", "neu")}'
        f"{budget_pill}"
        f"</div>"
        f'<div class="pcard-grid">'
        f'<div><span class="pk">买入区间</span><span class="pv">{_money(it.get("entry"))}</span></div>'
        f'<div><span class="pk">止损</span><span class="pv neg">{_money(it.get("stop_loss"))}</span></div>'
        f'<div><span class="pk">止盈</span><span class="pv pos">{_money(it.get("take_profit"))}</span></div>'
        f'<div><span class="pk">20日涨跌</span><span class="pv {_pvcls(it.get("ret_20d"))}">{_pct(it.get("ret_20d"))}</span></div>'
        f"</div>"
        f'<div class="pcard-reason">{views.esc(_reason(it))}</div>'
        f"</div>"
    )


def _reject_html(it: dict[str, Any], cash: float, note: str = "") -> str:
    code = str(it.get("code") or "")[-6:]
    name = it.get("name") or code
    gate = it.get("gate") or {}
    reason = note or gate.get("reason") or it.get("risk_notes") or "综合分未达门槛"
    score = it.get("score")
    return (
        f'<div class="prow">'
        f'<span class="pr-name">{views.esc(name)} <span class="pr-code">{views.esc(code)}</span></span>'
        f'<span class="pr-score">{("—" if score is None else f"{float(score):.0f}")} 分</span>'
        f'<span class="pr-reason">{views.esc(reason)}</span>'
        f"</div>"
    )


def _reason(it: dict) -> str:
    """卡片里的研判理由；经济档（仅 Analyst 用大模型）把措辞改准确。"""
    text = str(it.get("rationale") or it.get("summary") or "—")
    if st.session_state.get("_sig_tier") == "eco":
        text = text.replace(
            "本建议由规则路径生成，未使用大模型。",
            "舆情研判由本地大模型完成；策略与校验走规则（经济档）。",
        )
    return text


def render() -> None:
    sig, as_of = data.latest_signal()
    if not sig:
        st.info(
            "还没有每日推荐文件。先在「数据更新」页跑一次数据更新，"
            "或命令行执行 `python scripts/run_daily_signals.py --limit 20`。"
        )
        return

    items = sig.get("items") or []
    summary = sig.get("summary") or {}

    # ---- 资金约束：拿模拟账户的可用现金 ----
    cash = paper.cash_available()
    usable = [i for i in items if str(i.get("verdict")) != "reject"]
    poor = [i for i in usable if not plan_buy(i.get("close"), i.get("position_pct"), cash)["lots"]]
    rich = [i for i in usable if i not in poor]
    buy = [i for i in rich if str(i.get("verdict")) == "buy"]
    watch = [i for i in rich if str(i.get("verdict")) == "watch"]
    reject = [i for i in items if str(i.get("verdict")) == "reject"]

    _tier = str(sig.get("llm_tier") or "none")
    st.session_state["_sig_tier"] = _tier
    _tier_cn = {"none": "规则研判", "eco": "大模型·经济档（仅 Analyst）",
                "max": "大模型·完整档（三层）"}.get(_tier, _tier)
    layout.page_head(
        "今日建议入手",
        f"数据截止 {as_of} · 四因子加权（舆情 35% / 趋势 25% / 量能 20% / 风险 20%）"
        f" · {_tier_cn} · 每条都先过新闻闸门 · 只推荐「当前现金买得起 1 手」的标的",
    )
    layout.md(layout.chips(as_of, len(buy), len(watch)))

    # 研判档位：决定这份推荐是规则跑的还是带大模型的；改档位后可原地重算
    _opts = {"none": "规则（快，约 30 秒）", "eco": "经济档 eco（仅 Analyst，约 6–8 分钟）",
             "max": "完整档 max（三层，约 12–15 分钟）"}
    c_tier, c_go, c_tip = st.columns([1.5, 1, 3.2], gap="medium", vertical_alignment="bottom")
    with c_tier:
        _picked = st.pills(
            "研判档位", list(_opts),
            default=_tier if _tier in _opts else "none",
            format_func=lambda k: {"none": "规则", "eco": "eco", "max": "max"}[k],
            key="picks_tier",
            help="规则 ~30 秒 / eco 6–8 分钟 / max 12–15 分钟；换档位后点右侧「按此档位重算」",
        )
        picked_tier = _picked or (_tier if _tier in _opts else "none")
    with c_go:
        rerun = st.button("按此档位重算", key="picks_rerun", use_container_width=True)
    with c_tip:
        st.caption(
            f"当前选择：{_opts[picked_tier]}。重算期间请勿关闭页面（关页面会连带退出后端）。"
        )
    if rerun:
        st.session_state["db_busy"] = True
        data.clear_caches()
        try:
            ok = _run_jobs([
                ("重算每日建议（%s）" % _opts[picked_tier].split("（")[0],
                 [PY, "-u", "scripts/run_daily_signals.py", "--limit", "20",
                  "--llm", picked_tier]),
            ])
        finally:
            st.session_state["db_busy"] = False
            data.clear_caches()
        if ok:
            st.success("已按新档位重算完成。")
            st.rerun()
        else:
            st.error("重算失败，请看上方日志尾部。")
    layout.md(
        views.metrics_row([
            (f"{cash:,.0f}", "可用现金（元）"),
            (str(len(buy)), "可买·建议买入"),
            (str(len(watch)), "可买·观察"),
            (str(len(poor)), "资金不足"),
            (str(sig.get("generated_at", "—"))[11:16] or "—", "生成时间"),
        ])
    )
    if not usable:
        st.info("今天的候选池里没有非否决标的，无需配置资金。")
    elif not rich:
        cheapest = sorted(usable, key=lambda i: lot_cost(i.get("close")) or 1e9)[:3]
        st.warning(
            f"当前可用现金 {cash:,.2f} 元，候选池里没有一只买得起 1 手。"
            "最便宜的三只是：" + "、".join(
                f"{c.get('name') or c.get('code')}（1 手 {_money(lot_cost(c.get('close')))} 元）"
                for c in cheapest
            ) + "。去「模拟持仓 → 账户设置」调高初始资金即可看到完整推荐。"
        )
    elif poor:
        st.caption(
            f"已按可用现金 {cash:,.2f} 元过滤：{len(poor)} 只标的 1 手就超出预算，"
            "统一收进「资金不足区」，不占你的注意力。"
        )

    # ---- 板块筛选：科创板 / 创业板 / 主板 / 北交所 ----
    def _bd(it: dict[str, Any]) -> str:
        return boards.board_of(str(it.get("code") or ""))

    counts: dict[str, int] = {}
    for it in rich:
        counts[_bd(it)] = counts.get(_bd(it), 0) + 1
    options = ["全部"] + [b for b in boards.BOARD_ORDER if counts.get(b)]
    picked = st.pills(
        "板块", options, default="全部", key="board_filter",
        format_func=lambda b: (f"全部 {len(rich)}" if b == "全部"
                               else f"{b} {counts.get(b, 0)}"),
    ) or "全部"
    if picked != "全部" and boards.board_hint(picked):
        st.caption(boards.board_hint(picked))
    if picked != "全部":
        buy = [i for i in buy if _bd(i) == picked]
        watch = [i for i in watch if _bd(i) == picked]

    def _cards(rows: list[dict[str, Any]]) -> None:
        for start in range(0, len(rows), 3):
            cols = st.columns(3, gap="medium")
            for col, it in zip(cols, rows[start:start + 3]):
                with col:
                    layout.md(_card_html(it, cash))
                    plan = plan_buy(it.get("close"), it.get("position_pct"), cash)
                    if st.button(
                        "＋ 模拟买入", key=f"pbuy_{it.get('code')}",
                        use_container_width=True,
                        help=(f"按现价记 {plan['lots']} 手（{_money(plan['amount'])} 元）、"
                              f"持有 10 天的模拟持仓" if plan["lots"]
                              else "当前现金买不起 1 手"),
                        disabled=not plan["lots"],
                    ):
                        try:
                            paper.add_position(
                                str(it.get("code")), it.get("name") or "",
                                float(it.get("close") or 0), holding_days=10,
                                amount=max(plan["amount"], plan["hand"] or 0),
                                source=f"每日建议 {as_of}",
                                thesis=str(it.get("rationale") or "")[:120],
                            )
                            st.toast(f"已记入模拟持仓：{it.get('name') or it.get('code')}")
                        except Exception as exc:  # noqa: BLE001
                            st.error(f"记录失败：{exc}")

    if buy:
        st.markdown("#### 建议买入")
        _cards(buy)
    elif rich:
        st.info(
            "今日没有达到「建议买入」门槛（分数 ≥65 且闸门通过）的标的，"
            "下面是可以观察的名单——宁可错过，不要勉强。"
        )

    if watch:
        st.markdown("#### 观察名单")
        _cards(watch)

    if poor:
        st.markdown("#### 资金不足区")
        st.caption(
            f"以下标的信号成立，但 1 手成本超过当前可用现金 {cash:,.2f} 元；"
            "调高初始资金或等它们跌下来再看。"
        )
        layout.md("".join(
            _reject_html(
                i, cash,
                f"1 手约需 {_money(lot_cost(i.get('close')))} 元，超出可用现金",
            )
            for i in poor
        ))

    if reject:
        st.markdown("#### 今日否决区")
        st.caption("被新闻闸门拦下或综合分不足的标的——记下原因，比记建议更有用。")
        layout.md("".join(_reject_html(i, cash) for i in reject))

    layout.md(
        '<p class="disclaimer">以上为程序化规则输出，仅用于毕业设计的学术演示，'
        "不构成任何投资建议。股市有风险，入市需谨慎。</p>"
    )
