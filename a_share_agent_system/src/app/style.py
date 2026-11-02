# -*- coding: utf-8 -*-
"""style.py — 苹果官网简约风格的 CSS 注入

设计语言（对齐 apple.com/cn）：
- 大留白、细分隔线、圆角卡片（18px）、无重阴影
- 主文字 #1d1d1f / 次级文字 #6e6e73 / 区块底色 #f5f5f7 / 强调蓝 #0071e3
- 系统字体栈：SF Pro / PingFang SC / Microsoft YaHei
- 涨跌色遵循 A 股惯例：涨红 #ff453a / 跌绿 #00b050

用法: 在 main.py 顶部调用 `inject_apple_style()` 一次即可。
"""

from __future__ import annotations

import streamlit as st

# 系统字体栈（macOS 用 SF Pro，Windows 回退 PingFang/雅黑）
FONT_STACK = (
    '-apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", '
    '"PingFang SC", "Helvetica Neue", "Microsoft YaHei", sans-serif'
)

# A 股涨跌色（红涨绿跌）
UP_RED = "#ff453a"
DOWN_GREEN = "#00b050"
ACCENT_BLUE = "#0071e3"

_CSS = f"""
<style>
/* ---------- 去掉 Streamlit 默认外壳 ---------- */
#header, #footer, #MainMenu, [data-testid="stHeader"],
[data-testid="stToolbar"], [data-testid="stDecoration"] {{
    visibility: hidden; height: 0 !important;
}}
.block-container {{
    padding-top: 2.2rem !important;
    padding-bottom: 6rem !important;
    max-width: 1120px !important;
    font-family: {FONT_STACK};
}}
section[data-testid="stSidebar"] {{
    background: #ffffff;
    border-right: 1px solid #d2d2d7;
}}
section[data-testid="stSidebar"] * {{
    font-family: {FONT_STACK};
}}
[data-testid="stSidebar"] .block-container {{
    padding-top: 2rem;
}}
h1, h2, h3, h4, p, span, div, button, th, td, li, label, input {{
    font-family: {FONT_STACK} !important;
}}

/* ---------- Hero 区（苹果式大标题） ---------- */
.hero {{
    text-align: center;
    padding: 2.2rem 1rem 1.6rem;
}}
.hero h1 {{
    font-size: 2.6rem;
    font-weight: 600;
    letter-spacing: -0.02em;
    color: #1d1d1f;
    margin: 0 0 0.4rem;
    line-height: 1.15;
}}
.hero .sub {{
    font-size: 1.15rem;
    font-weight: 400;
    color: #6e6e73;
    margin: 0 auto 0.8rem;
    max-width: 640px;
    line-height: 1.5;
}}
.hero .meta {{
    font-size: 0.8rem;
    color: #86868b;
    margin: 0;
}}
.hero .meta b {{ color: #1d1d1f; font-weight: 500; }}

/* ---------- 苹果式横排小指标（Overview） ---------- */
.metrics {{
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    gap: 1px;
    background: #d2d2d7;              /* 用 1px 缝隙当分隔线 */
    border: 1px solid #d2d2d7;
    border-radius: 18px;
    overflow: hidden;
    margin: 1.2rem 0 1.6rem;
}}
.metrics .cell {{
    flex: 1 1 0;
    min-width: 130px;
    background: #ffffff;
    padding: 1.1rem 1rem;
    text-align: center;
}}
.metrics .val {{
    font-size: 1.7rem;
    font-weight: 600;
    color: #1d1d1f;
    letter-spacing: -0.01em;
    line-height: 1.2;
}}
.metrics .val .unit {{ font-size: 0.95rem; font-weight: 500; color: #6e6e73; }}
.metrics .lab {{
    font-size: 0.78rem;
    color: #6e6e73;
    margin-top: 0.3rem;
}}

/* ---------- 区块标题（仿 apple.com 二级标题） ---------- */
.section-title {{
    font-size: 1.5rem;
    font-weight: 600;
    color: #1d1d1f;
    letter-spacing: -0.01em;
    margin: 2.6rem 0 0.3rem;
}}
.section-sub {{
    font-size: 0.95rem;
    color: #6e6e73;
    margin: 0 0 1rem;
    line-height: 1.5;
}}

/* ---------- 卡片 ---------- */
.card {{
    background: #ffffff;
    border: 1px solid #d2d2d7;
    border-radius: 18px;
    padding: 1.4rem 1.6rem;
    margin-bottom: 1rem;
}}
.card.gray {{ background: #f5f5f7; border-color: #e8e8ed; }}
.card h3 {{
    font-size: 1.05rem;
    font-weight: 600;
    color: #1d1d1f;
    margin: 0 0 0.8rem;
}}
.card p {{
    font-size: 0.92rem;
    line-height: 1.65;
    color: #424245;
    margin: 0.35rem 0;
}}
.card .muted {{ color: #6e6e73; font-size: 0.85rem; }}
.card .kv {{
    display: flex;
    justify-content: space-between;
    padding: 0.45rem 0;
    border-bottom: 1px solid #e8e8ed;
    font-size: 0.9rem;
}}
.card .kv:last-child {{ border-bottom: none; }}
.card .kv .k {{ color: #6e6e73; }}
.card .kv .v {{ color: #1d1d1f; font-weight: 500; text-align: right; }}

/* ---------- 苹果式胶囊徽章 ---------- */
.pill {{
    display: inline-block;
    padding: 0.22rem 0.8rem;
    border-radius: 999px;
    font-size: 0.8rem;
    font-weight: 500;
    line-height: 1.4;
    margin-right: 0.4rem;
    white-space: nowrap;
}}
.pill.pos   {{ background: #ffebe9; color: #c4180f; }}        /* 利多=红 */
.pill.neg   {{ background: #e2f6ea; color: #046b36; }}        /* 利空=绿 */
.pill.neu   {{ background: #e8e8ed; color: #6e6e73; }}        /* 中性=灰 */
.pill.long  {{ background: #ffebe9; color: #c4180f; }}
.pill.short {{ background: #e2f6ea; color: #046b36; }}
.pill.watch {{ background: #e8e8ed; color: #6e6e73; }}
.pill.pass  {{ background: #eaf3ff; color: #0066cc; }}
.pill.fail  {{ background: #ffebe9; color: #c4180f; }}
.pill.blue  {{ background: #eaf3ff; color: #0066cc; }}

/* ---------- 数字涨跌色 ---------- */
.up   {{ color: {UP_RED}; font-weight: 600; }}
.down {{ color: {DOWN_GREEN}; font-weight: 600; }}
.flat {{ color: #6e6e73; font-weight: 500; }}

/* ---------- 迭代轮次时间线 ---------- */
.round-item {{
    border-left: 2px solid #d2d2d7;
    padding: 0.1rem 0 1.1rem 1.1rem;
    position: relative;
    margin-left: 0.3rem;
}}
.round-item::before {{
    content: "";
    position: absolute;
    left: -6px;
    top: 0.35rem;
    width: 10px; height: 10px;
    border-radius: 50%;
    background: {ACCENT_BLUE};
}}
.round-item.pass::before {{ background: {ACCENT_BLUE}; }}
.round-item.fail::before {{ background: {UP_RED}; }}
.round-item .r-head {{
    font-size: 0.85rem; color: #6e6e73;
    display: flex; gap: 0.7rem; align-items: baseline;
    margin-bottom: 0.25rem;
}}
.round-item .r-head b {{ color: #1d1d1f; font-size: 0.95rem; }}
.round-item p {{ font-size: 0.87rem; line-height: 1.55; margin: 0.15rem 0; }}

/* ---------- 全部股票一览表 ---------- */
table.stocks {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.88rem;
}}
table.stocks th {{
    text-align: left;
    color: #6e6e73;
    font-weight: 500;
    padding: 0.6rem 0.7rem;
    border-bottom: 1px solid #d2d2d7;
}}
table.stocks td {{
    padding: 0.65rem 0.7rem;
    border-bottom: 1px solid #e8e8ed;
    color: #1d1d1f;
}}
table.stocks tr:last-child td {{ border-bottom: none; }}
table.stocks td.num {{ font-variant-numeric: tabular-nums; }}

/* ---------- 证据条目 ---------- */
.evidence {{
    border: 1px solid #e8e8ed;
    border-radius: 12px;
    padding: 0.8rem 1rem;
    margin-bottom: 0.6rem;
    background: #fbfbfd;
}}
.evidence .ev-head {{
    display: flex; justify-content: space-between; align-items: baseline;
    gap: 0.8rem;
}}
.evidence .ev-title {{
    font-size: 0.9rem; font-weight: 500; color: #1d1d1f; line-height: 1.45;
}}
.evidence .ev-meta {{
    font-size: 0.75rem; color: #86868b; white-space: nowrap;
}}
.evidence .ev-snip {{
    font-size: 0.82rem; color: #6e6e73; line-height: 1.55;
    margin-top: 0.35rem;
}}

/* ---------- Streamlit 组件微调 ---------- */
/* Material Symbols 字体加载失败时，expander 图标连字文本会以纯文本
   「keyboard_arrow_right」显示并与标题重叠 → 隐藏文本，改用 CSS 三角箭头 */
[data-testid="stExpander"] [data-testid="stIconMaterial"] {{
    font-size: 0 !important;
    width: 14px;
    height: 14px;
    position: relative;
    flex-shrink: 0;
    margin-right: 6px;
}}
[data-testid="stExpander"] [data-testid="stIconMaterial"]::before {{
    content: "";
    position: absolute;
    top: 3px;
    left: 2px;
    width: 7px;
    height: 7px;
    border-right: 1.8px solid #6e6e73;
    border-bottom: 1.8px solid #6e6e73;
    transform: rotate(-45deg);      /* 收起：指向右 */
    transition: transform 0.15s ease;
}}
[data-testid="stExpander"] details[open] [data-testid="stIconMaterial"]::before {{
    transform: rotate(45deg);       /* 展开：指向下 */
    top: 1px;
}}
[data-testid="stExpander"] {{
    border: 1px solid #e8e8ed;
    border-radius: 14px;
    background: #ffffff;
}}
[data-testid="stExpander"] summary {{
    font-weight: 500; color: #1d1d1f;
}}
.stTabs [data-baseweb="tab-list"] {{ gap: 0; border-bottom: 1px solid #d2d2d7; }}
.stTabs [data-baseweb="tab"] {{
    background: transparent;
    font-size: 0.95rem;
    font-weight: 500;
    color: #6e6e73;
    padding: 0.6rem 1.1rem;
    border-radius: 10px 10px 0 0;
}}
.stTabs [aria-selected="true"] {{
    color: {ACCENT_BLUE} !important;
    border-bottom: 2px solid {ACCENT_BLUE} !important;
    background: transparent;
}}
div[data-testid="stPlotlyChart"] {{
    border: 1px solid #e8e8ed;
    border-radius: 18px;
    padding: 0.6rem 0.5rem 0.2rem;
    background: #ffffff;
}}
</style>
"""


def inject_apple_style() -> None:
    """注入全局苹果风格 CSS（每个 Streamlit 会话调用一次即可）。"""
    st.markdown(_CSS, unsafe_allow_html=True)
