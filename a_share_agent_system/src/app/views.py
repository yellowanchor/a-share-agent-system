# -*- coding: utf-8 -*-
"""views.py — 苹果风格的 HTML 片段渲染（纯函数，无副作用）

所有函数返回 HTML 字符串，由 main.py 用 st.markdown(..., unsafe_allow_html=True)
注入。动态文本一律 html.escape，防注入/防错位。
"""

from __future__ import annotations

import html
from typing import Any

from src.app.data import fmt_num


def esc(v: Any) -> str:
    return html.escape(str(v) if v is not None else "")


# ---------------------------------------------------------------- 通用组件
def hero(title: str, subtitle: str, meta: list[tuple[str, str]]) -> str:
    """苹果式首页大标题区。meta: [(标签, 值), ...]"""
    meta_html = "　·　".join(
        f"<b>{esc(k)}</b> {esc(v)}" for k, v in meta
    )
    return f"""
    <div class="hero">
      <h1>{esc(title)}</h1>
      <p class="sub">{esc(subtitle)}</p>
      <p class="meta">{meta_html}</p>
    </div>
    """


def metrics_row(items: list[tuple[str, str]]) -> str:
    """横排指标条。items: [(值 HTML, 标签), ...]"""
    cells = "".join(
        f'<div class="cell"><div class="val">{v}</div>'
        f'<div class="lab">{esc(lab)}</div></div>'
        for v, lab in items
    )
    return f'<div class="metrics">{cells}</div>'


def section(title: str, sub: str = "") -> str:
    s = f'<p class="section-sub">{esc(sub)}</p>' if sub else ""
    return f'<h2 class="section-title">{esc(title)}</h2>{s}'


def pill(text: str, kind: str) -> str:
    return f'<span class="pill {kind}">{esc(text)}</span>'


def pct_span(pct: float | None, suffix: str = "%") -> str:
    """涨跌幅着色：红涨绿跌灰平。"""
    if pct is None:
        return '<span class="flat">—</span>'
    cls = "up" if pct > 0 else ("down" if pct < 0 else "flat")
    sign = "+" if pct > 0 else ""
    return f'<span class="{cls}">{sign}{pct:.2f}{suffix}</span>'


SENTIMENT_PILL = {
    "positive": ("利多", "pos"),
    "negative": ("利空", "neg"),
    "neutral": ("中性", "neu"),
}
DIRECTION_PILL = {
    "long": ("做多", "long"),
    "short": ("做空", "short"),
    "watch": ("观望", "watch"),
}


def sentiment_pill(s: str | None) -> str:
    label, kind = SENTIMENT_PILL.get(s or "neutral", ("中性", "neu"))
    return pill(label, kind)


def direction_pill(d: str | None) -> str:
    label, kind = DIRECTION_PILL.get(d or "watch", ("观望", "watch"))
    return pill(label, kind)


def empty_financial_card() -> str:
    """无 PIT 财务数据时的空卡片。"""
    return _card(
        "财务快照（Point-in-Time）",
        "<p class='muted'>（该股票无 Point-in-Time 财务数据）</p>",
    )


def pill_row_summary(
    rep: dict[str, Any], adv: dict[str, Any], verdict: dict[str, Any]
) -> str:
    """当前股票的舆情→方向→校验结论摘要行。"""
    return (
        '<p style="margin:-0.4rem 0 1rem;font-size:0.95rem">'
        f'舆情 {sentiment_pill(rep.get("sentiment"))}　'
        f'方向 {direction_pill(adv.get("direction"))}　'
        f'{pill("校验通过", "pass") if verdict.get("passed") else pill("校验未通过", "fail")}'
        '</p>'
    )


def kv_row(k: str, v: str) -> str:
    return f'<div class="kv"><span class="k">{esc(k)}</span><span class="v">{v}</span></div>'


def _card(title: str, body: str, gray: bool = False) -> str:
    cls = "card gray" if gray else "card"
    return f'<div class="{cls}"><h3>{esc(title)}</h3>{body}</div>'


# ---------------------------------------------------------------- 业务视图
def market_snapshot(m: dict[str, Any]) -> str:
    """行情快照卡（Analyst 采集）。"""
    rows = "".join([
        kv_row("收盘价（前复权）", f'{fmt_num(m.get("close"))} 元'),
        kv_row("近5日涨跌", pct_span(m.get("ret_5d"))),
        kv_row("近20日涨跌", pct_span(m.get("ret_20d"))),
        kv_row("RSI(14)", fmt_num(m.get("rsi14"))),
        kv_row("ATR(14)", f'{fmt_num(m.get("atr14"))} 元 '
                          f'({fmt_num(m.get("atr_pct"))}%)'),
        kv_row("支撑 / 阻力",
               f'{fmt_num(m.get("support"))} / {fmt_num(m.get("resistance"))}'),
        kv_row("年化波动率", fmt_num(m.get("annual_vol"))),
    ])
    return _card("行情快照", rows, gray=True)


def financial_snapshot(fin: dict[str, Any]) -> str:
    """财务快照卡（Point-in-Time 对齐后）。"""
    rows = "".join([
        kv_row("报告期 / 披露日",
               f'{esc(fin.get("report_date"))} / {esc(fin.get("pub_date"))}'),
        kv_row("EPS（每股收益）", f'{fmt_num(fin.get("eps"))} 元'),
        kv_row("营收（同比）",
               f'{fmt_num(fin.get("revenue") / 1e8, 1)} 亿元 '
               f'({pct_span(fin.get("revenue_yoy"))})'),
        kv_row("归母净利（同比）",
               f'{fmt_num((fin.get("net_profit") or 0) / 1e8, 1)} 亿元 '
               f'({pct_span(fin.get("net_profit_yoy"))})'),
        kv_row("ROE / 毛利率",
               f'{fmt_num(fin.get("roe"))}% / {fmt_num(fin.get("gross_margin"))}%'),
    ])
    return _card("财务快照（Point-in-Time）", rows, gray=True)


def analyst_view(rep: dict[str, Any]) -> str:
    """舆情研判卡（Analyst）。"""
    conf = rep.get("confidence")
    valid = esc(rep.get("valid_until") or "—")
    head = (
        f'{sentiment_pill(rep.get("sentiment"))}'
        f'{pill(f"置信度 {conf:.0%}" if conf is not None else "置信度 —", "blue")}'
        f'{pill(f"有效期：{valid}", "neu")}'
    )
    themes = "".join(pill(esc(t), "neu") for t in (rep.get("themes") or []))
    stats = rep.get("sentiment_stats") or {}
    n = stats.get("n", 0)
    dist = (
        f"证据 {n} 条 · 利多 {stats.get('positive', 0)} · "
        f"利空 {stats.get('negative', 0)} · 中性 {stats.get('neutral', 0)}"
        if n else "（无词典打标数据）"
    )
    llm = "" if rep.get("llm_used") else pill("规则降级（LLM 未参与）", "fail")
    body = f"""
    <p>{head}</p>
    <p>{esc(rep.get("summary") or "")}</p>
    <p class="muted">词典情感分布：{esc(dist)} {llm}</p>
    <p class="muted">主题标签：{themes or "—"}</p>
    """
    return _card("舆情研判 · Analyst", body)


def strategist_view(adv: dict[str, Any]) -> str:
    """策略建议卡（Strategist）。"""
    pos = adv.get("position_pct")
    rows = "".join([
        kv_row("建议方向", direction_pill(adv.get("direction"))),
        kv_row("建议仓位", f"{fmt_num(pos, 1)}%" if pos is not None else "—"),
        kv_row("入场参考", f'{fmt_num(adv.get("entry"))} 元'),
        kv_row("止损 / 止盈",
               f'{fmt_num(adv.get("stop_loss"))} / {fmt_num(adv.get("take_profit"))} 元'),
        kv_row("持有周期", esc(adv.get("holding_period") or "—")),
    ])
    body = f"""
    <p>{direction_pill(adv.get("direction"))}
        {pill(f"仓位 {fmt_num(pos, 1)}%" if pos is not None else "仓位 —", "blue")}
        {pill(f"信心 {adv.get('confidence'):.0%}" if adv.get("confidence") is not None else "", "neu")}</p>
    {rows}
    <p><span class="k muted">逻辑依据：</span>{esc(adv.get("rationale") or "—")}</p>
    <p><span class="k muted">失效条件：</span>{esc(adv.get("invalidation") or "—")}</p>
    <p><span class="k muted">风险提示：</span>{esc(adv.get("risk_notes") or "—")}</p>
    """
    return _card("策略建议 · Strategist", body)


def critic_view(verdict: dict[str, Any]) -> str:
    """校验结论卡（Critic）。"""
    issues = verdict.get("issues") or []
    n_block = sum(1 for i in issues if i.get("severity") == "blocker")
    n_warn = sum(1 for i in issues if i.get("severity") == "warning")
    n_info = sum(1 for i in issues if i.get("severity") == "info")
    head = (
        f'{pill("校验通过", "pass") if verdict.get("passed") else pill("校验未通过", "fail")}'
        f'{pill(f"阻断 {n_block}", "fail") if n_block else ""}'
        f'{pill(f"提示 {n_warn + n_info}", "neu") if n_warn + n_info else ""}'
    )
    if not issues:
        item_html = "<p class='muted'>无问题记录。</p>"
    else:
        item_html = "".join(
            f"""
            <div class="kv">
              <span class="k">{pill(esc(i.get('code')), 'neu')}
                {esc(i.get('severity'))}
                {('· ' + esc(i['source'])) if i.get('source') else ''}</span>
              <span class="v" style="text-align:left;flex:1;padding-left:1rem">
                {esc(i.get('message') or '')}
                <span class="muted">→ {esc(i.get('fix') or '')}</span>
              </span>
            </div>
            """
            for i in issues
        )
    body = f"<p>{head}</p><p>{esc(verdict.get('summary') or '')}</p>{item_html}"
    return _card("校验结论 · Critic", body)


def rounds_timeline(rounds: list[dict[str, Any]]) -> str:
    """Strategist ↔ Critic 迭代闭环时间线。"""
    if not rounds:
        return "<p class='muted'>（无迭代记录）</p>"
    items = []
    for rd in rounds:
        adv = rd.get("advice") or {}
        verdict = rd.get("verdict") or {}
        passed = verdict.get("passed")
        issues = verdict.get("issues") or []
        codes = ", ".join(
            f"{i.get('code')}({i.get('severity')})" for i in issues
        ) or "无"
        fb = rd.get("feedback")
        ms = rd.get("elapsed_ms")
        item = f"""
        <div class="round-item {'pass' if passed else 'fail'}">
          <div class="r-head">
            <b>第 {rd.get('round')} 轮</b>
            <span>{direction_pill(adv.get('direction'))}</span>
            <span>仓位 {fmt_num(adv.get('position_pct'), 1)}%</span>
            <span>{'✓ 通过' if passed else '✗ 需修正'}</span>
            <span>温度 {fmt_num(rd.get('temperature'), 2)}</span>
            <span>{fmt_num((ms or 0) / 1000, 1)}s</span>
          </div>
          <p>触发问题：{esc(codes)}</p>
          {"<p class='muted'>反馈：" + esc(fb) + "</p>" if fb else ""}
        </div>
        """
        items.append(item)
    return "".join(items)


def evidence_list(evidence: list[dict[str, Any]]) -> str:
    """证据链列表（RAG 检索 + 词典打标）。"""
    if not evidence:
        return "<p class='muted'>（未检索到证据）</p>"
    items = []
    for ev in evidence:
        sent = sentiment_pill(ev.get("sentiment"))
        meta_bits = [f"#{ev.get('idx')}", esc(ev.get("source") or ""),
                     esc(ev.get("publish_time") or "")]
        if ev.get("retrieval"):
            meta_bits.append(esc(ev["retrieval"]))
        if ev.get("score") is not None:
            meta_bits.append(f"相关度 {float(ev['score']):.3f}")
        items.append(f"""
        <div class="evidence">
          <div class="ev-head">
            <span class="ev-title">{esc(ev.get("title") or "")}</span>
            <span class="ev-meta">{' · '.join(b for b in meta_bits if b)}</span>
          </div>
          <p style="margin:0.3rem 0 0">{sent}
            <span class="muted">置信度 {fmt_num(ev.get('confidence'))}</span></p>
          <p class="ev-snip">{esc((ev.get("snippet") or "")[:200])}</p>
        </div>
        """)
    return "".join(items)


def stocks_table(results: list[dict[str, Any]], names: dict[str, str]) -> str:
    """全部股票一览表。"""
    rows = []
    for r in results:
        rep = r.get("report") or {}
        adv = r.get("advice") or {}
        verdict = r.get("verdict") or {}
        n_rounds = len(r.get("rounds") or [])
        code = esc(r.get("stock_code"))
        passed = (
            '<span class="pill pass">通过</span>'
            if verdict.get("passed") else '<span class="pill fail">未过</span>'
        )
        rows.append(f"""
        <tr>
          <td class="num">{code}</td>
          <td>{esc(names.get(r.get("stock_code"), ""))}</td>
          <td>{sentiment_pill(rep.get("sentiment"))}</td>
          <td>{direction_pill(adv.get("direction"))}</td>
          <td class="num">{fmt_num(adv.get("position_pct"), 1)}%</td>
          <td class="num">{n_rounds}</td>
          <td>{passed}</td>
        </tr>""")
    return f"""
    <table class="stocks">
      <thead><tr>
        <th>代码</th><th>名称</th><th>舆情</th><th>方向</th>
        <th>仓位</th><th>迭代轮数</th><th>终局</th>
      </tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    """
