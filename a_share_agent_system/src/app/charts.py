# -*- coding: utf-8 -*-
"""charts.py — Plotly 图表（苹果简约风格 + A 股红涨绿跌）

- kline_chart:      前复权 K 线 + 成交量 + MA + 策略价位线（止损/止盈/支撑/阻力）
- sentiment_chart:  情感趋势时间线（日频利多/利空条形 + 7 日净情绪均线）
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from src.app.style import DOWN_GREEN, FONT_STACK, UP_RED

GRID = "#e8e8ed"
AXIS = "#86868b"
INK = "#1d1d1f"
ACCENT = "#0071e3"


def _base_layout(fig: go.Figure, height: int = 420) -> None:
    """统一苹果式版式：白底、细虚线网格、浅灰轴线、系统字体。"""
    fig.update_layout(
        template="plotly_white",
        height=height,
        margin=dict(l=8, r=16, t=30, b=8),
        font=dict(family=FONT_STACK, size=12, color=INK),
        plot_bgcolor="#ffffff",
        paper_bgcolor="#ffffff",
        hoverlabel=dict(
            font=dict(family=FONT_STACK, size=12), bgcolor="#ffffff",
            bordercolor=GRID,
        ),
        xaxis=dict(
            showgrid=True, gridcolor=GRID, gridwidth=1, zeroline=False,
            linecolor=GRID, tickfont=dict(color=AXIS), showspikes=True,
            spikemode="across", spikethickness=1, spikedash="dot",
            spikecolor=GRID,
        ),
        legend=dict(
            orientation="h", y=1.06, x=0, font=dict(color=AXIS, size=11),
            bgcolor="rgba(0,0,0,0)", itemsizing="constant",
        ),
    )


# ---------------------------------------------------------------- K 线
def kline_chart(
    df: pd.DataFrame,
    as_of: str,
    advice: dict[str, Any] | None = None,
    market: dict[str, Any] | None = None,
    show_ma: tuple[str, ...] = ("MA5", "MA20", "MA60"),
) -> go.Figure:
    """前复权 K 线 + 成交量。

    Args:
        df:      data.load_kline 的返回（正序）
        as_of:   数据截止日（画竖线）
        advice:  Strategist 建议（画止损/止盈/入场横线）
        market:  Analyst 行情快照（画支撑/阻力虚线）
        show_ma: 要显示的均线
    """
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.75, 0.25], vertical_spacing=0.03,
    )
    d = df.copy()
    d["date_str"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
    up = d["close"] >= d["open"]

    # ---- 蜡烛（红涨绿跌） ----
    fig.add_trace(
        go.Candlestick(
            x=d["date_str"], open=d["open"], high=d["high"],
            low=d["low"], close=d["close"],
            increasing=dict(line=dict(color=UP_RED, width=1),
                            fillcolor=UP_RED),
            decreasing=dict(line=dict(color=DOWN_GREEN, width=1),
                            fillcolor=DOWN_GREEN),
            name="前复权K线", showlegend=False,
            hovertext=[f"{dt}<br>开 {o:,.2f} 高 {h:,.2f}<br>"
                       f"低 {l:,.2f} 收 {c:,.2f}<br>涨跌 {p:+.2f}%"
                       for dt, o, h, l, c, p in zip(
                           d["date_str"], d["open"], d["high"], d["low"],
                           d["close"], d["pct_chg"])],
            hoverinfo="text",
        ),
        row=1, col=1,
    )

    # ---- 均线 ----
    colors = {"MA5": "#86868b", "MA20": ACCENT, "MA60": "#bf5af2"}
    for ma, win in (("MA5", 5), ("MA20", 20), ("MA60", 60)):
        if ma in show_ma and len(d) >= win:
            fig.add_trace(
                go.Scatter(
                    x=d["date_str"], y=d["close"].rolling(win).mean(),
                    mode="lines", name=ma, line=dict(
                        color=colors[ma], width=1.2),
                ),
                row=1, col=1,
            )

    # ---- 成交量 ----
    fig.add_trace(
        go.Bar(
            x=d["date_str"], y=d["volume"] / 1e4,
            marker_color=[UP_RED if u else DOWN_GREEN for u in up],
            name="成交量(万手)", showlegend=True,
            hovertemplate="%{x}<br>成交量 %{y:,.0f} 万<extra></extra>",
        ),
        row=2, col=1,
    )

    # ---- 策略价位线（Strategist） ----
    # 标注位置按价位高低交替（上侧/下侧右端），避免多条价位线标注重叠。
    price_lines: list[tuple[str, float, str, str, str]] = []
    if advice:
        if advice.get("stop_loss") is not None:
            price_lines.append(
                ("止损", advice["stop_loss"], "#c4180f", "dash", "bottom right"))
        if advice.get("take_profit") is not None:
            price_lines.append(
                ("止盈", advice["take_profit"], "#046b36", "dash", "top right"))
        if advice.get("entry") is not None:
            price_lines.append(
                ("入场", advice["entry"], ACCENT, "dot", "bottom right"))
    if market:
        if market.get("support"):
            price_lines.append(
                ("支撑", market["support"], AXIS, "dot", "bottom right"))
        if market.get("resistance"):
            price_lines.append(
                ("阻力", market["resistance"], AXIS, "dot", "top right"))
    for label, y, color, dash, pos in price_lines:
        fig.add_hline(
            y=float(y), line=dict(color=color, width=1, dash=dash),
            annotation_text=f"{label} {float(y):,.2f}",
            annotation_position=pos,
            annotation_font=dict(color=color, size=11, family=FONT_STACK),
            row=1, col=1,
        )

    # ---- as_of 截止日竖线（Point-in-Time 边界） ----
    # 注意：类目轴上 add_vline 自带 annotation 会让 plotly 崩溃（对字符串求均值），
    # 因此拆成 vline（不带标注）+ add_annotation（显式定位）。
    as_of_str = str(as_of)[:10]
    if as_of_str in set(d["date_str"]):
        fig.add_vline(
            x=as_of_str, line=dict(color=INK, width=1, dash="dashdot"),
            row="all", col=1,
        )
        fig.add_annotation(
            x=as_of_str, xref="x", y=1, yref="y domain", yanchor="bottom",
            text="数据截止日", showarrow=False, xanchor="right",
            font=dict(color=INK, size=11, family=FONT_STACK),
        )

    _base_layout(fig, height=460)
    fig.update_xaxes(rangeslider_visible=False)
    fig.update_yaxes(
        showgrid=True, gridcolor=GRID, gridwidth=1, zeroline=False,
        tickfont=dict(color=AXIS), row=1, col=1,
    )
    fig.update_yaxes(showgrid=False, tickfont=dict(color=AXIS), row=2, col=1)
    return fig


# ---------------------------------------------------------------- 情感趋势
def sentiment_chart(daily: pd.DataFrame) -> go.Figure:
    """日频情感趋势：利多（红，向上）/ 利空（绿，向下）+ 7 日净情绪均线。"""
    fig = go.Figure()
    if daily.empty:
        _base_layout(fig, height=260)
        return fig

    x = [str(v) for v in daily["date"]]
    fig.add_trace(
        go.Bar(
            x=x, y=daily["positive"], name="利多(条)",
            marker_color=UP_RED, hovertemplate="%{x}<br>利多 %{y} 条<extra></extra>",
        )
    )
    fig.add_trace(
        go.Bar(
            x=x, y=-daily["negative"], name="利空(条)",
            marker_color=DOWN_GREEN,
            hovertemplate="%{x}<br>利空 %{customdata} 条<extra></extra>",
            customdata=daily["negative"],
        )
    )
    net = daily["net_score"].rolling(7, min_periods=1).mean()
    fig.add_trace(
        go.Scatter(
            x=x, y=net, name="7日净情绪(均线)",
            mode="lines", line=dict(color=INK, width=1.4, shape="spline"),
            hovertemplate="%{x}<br>净情绪 %{y:+.2f}<extra></extra>",
        )
    )
    fig.add_hline(
        y=0, line=dict(color=GRID, width=1),
        annotation_text="中性", annotation_position="bottom right",
        annotation_font=dict(color=AXIS, size=10, family=FONT_STACK),
    )
    _base_layout(fig, height=260)
    fig.update_layout(barmode="stack", bargap=0.4)
    fig.update_yaxes(
        showgrid=True, gridcolor=GRID, zeroline=False, tickfont=dict(color=AXIS),
        hoverformat="+.2f",
    )
    return fig


# ---------------------------------------------------------------- 概览小图
def batch_pass_chart(results: list[dict[str, Any]]) -> go.Figure:
    """全部股票首轮/终局通过率对比条形图（闭环增益可视化）。"""
    codes = [r["stock_code"] for r in results]
    first = [
        1 if (r.get("rounds") and r["rounds"][0]["verdict"].get("passed")) else 0
        for r in results
    ]
    final = [1 if r.get("verdict", {}).get("passed") else 0 for r in results]
    fig = go.Figure()
    fig.add_bar(
        x=codes, y=first, name="首轮通过",
        marker_color="#c7c7cc", hovertemplate="%{x} 首轮 %{y}<extra></extra>",
    )
    fig.add_bar(
        x=codes, y=final, name="终局通过",
        marker_color=ACCENT, hovertemplate="%{x} 终局 %{y}<extra></extra>",
    )
    fig.update_layout(barmode="group", bargap=0.35, bargroupgap=0.15)
    _base_layout(fig, height=280)
    fig.update_yaxes(
        showgrid=True, gridcolor=GRID, zeroline=True, zerolinecolor=GRID,
        tickfont=dict(color=AXIS),
        dtick=1, range=[-0.1, 1.4],
    )
    return fig
