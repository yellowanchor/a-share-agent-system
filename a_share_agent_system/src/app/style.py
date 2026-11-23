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
    background: var(--card-bg);
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
    color: var(--text);
    margin: 0 0 0.4rem;
    line-height: 1.15;
}}
.hero .sub {{
    font-size: 1.15rem;
    font-weight: 400;
    color: var(--muted);
    margin: 0 auto 0.8rem;
    max-width: 640px;
    line-height: 1.5;
}}
.hero .meta {{
    font-size: 0.8rem;
    color: #86868b;
    margin: 0;
}}
.hero .meta b {{ color: var(--text); font-weight: 500; }}

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
    background: var(--card-bg);
    padding: 1.1rem 1rem;
    text-align: center;
}}
.metrics .val {{
    font-size: 1.7rem;
    font-weight: 600;
    color: var(--text);
    letter-spacing: -0.01em;
    line-height: 1.2;
}}
.metrics .val .unit {{ font-size: 0.95rem; font-weight: 500; color: var(--muted); }}
.metrics .lab {{
    font-size: 0.78rem;
    color: var(--muted);
    margin-top: 0.3rem;
}}

/* ---------- 区块标题（仿 apple.com 二级标题） ---------- */
.section-title {{
    font-size: 1.5rem;
    font-weight: 600;
    color: var(--text);
    letter-spacing: -0.01em;
    margin: 2.6rem 0 0.3rem;
}}
.section-sub {{
    font-size: 0.95rem;
    color: var(--muted);
    margin: 0 0 1rem;
    line-height: 1.5;
}}

/* ---------- 卡片 ---------- */
.card {{
    background: var(--card-bg);
    border: 1px solid #d2d2d7;
    border-radius: 18px;
    padding: 1.4rem 1.6rem;
    margin-bottom: 1rem;
}}
.card.gray {{ background: #f5f5f7; border-color: #e8e8ed; }}
.card h3,
.card .card-title {{
    font-size: 1.05rem;
    font-weight: 600;
    color: var(--text);
    margin: 0 0 0.8rem;
}}
.card p {{
    font-size: 0.92rem;
    line-height: 1.65;
    color: #424245;
    margin: 0.35rem 0;
}}
.card .muted {{ color: var(--muted); font-size: 0.85rem; }}
.card .kv {{
    display: flex;
    justify-content: space-between;
    padding: 0.45rem 0;
    border-bottom: 1px solid var(--line);
    font-size: 0.9rem;
}}
.card .kv:last-child {{ border-bottom: none; }}

/* Critic 问题条目：上下堆叠，避免窄列里"一字一行" */
.card .issue {{
    padding: 0.55rem 0;
    border-bottom: 1px solid var(--line);
}}
.card .issue:last-child {{ border-bottom: none; }}
.card .issue-head {{
    display: flex;
    align-items: center;
    gap: 0.4rem;
    margin-bottom: 0.28rem;
    font-size: 0.8rem;
    color: var(--muted);
}}
.card .issue-head .sev {{ font-weight: 600; color: var(--muted); }}
.card .issue-msg {{
    font-size: 0.9rem;
    line-height: 1.62;
    color: #424245;
}}
.card .kv .k {{ color: var(--muted); }}
.card .kv .v {{ color: var(--text); font-weight: 500; text-align: right; }}

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
.pill.neu   {{ background: #e8e8ed; color: var(--muted); }}        /* 中性=灰 */
.pill.long  {{ background: #ffebe9; color: #c4180f; }}
.pill.short {{ background: #e2f6ea; color: #046b36; }}
.pill.watch {{ background: #e8e8ed; color: var(--muted); }}
.pill.pass  {{ background: #eaf3ff; color: #0066cc; }}
.pill.fail  {{ background: #ffebe9; color: #c4180f; }}
.pill.blue  {{ background: #eaf3ff; color: #0066cc; }}
.pill.good  {{ background: #ffebe9; color: #c4180f; }}        /* 结论=买入/看多，A股红 */
.pill.warn  {{ background: #fff4e5; color: #9a6700; }}        /* 观察 / 提示 */
.pill.bad   {{ background: #e8e8ed; color: var(--muted); }}        /* 否决 / 不建议 */
.pill.purple {{ background: #f3ecff; color: #5b3aa6; }}       /* 科创板 */
.pill.bd    {{ background: #f5f5f7; color: #3a3a3c; border: 1px solid #e3e3e8; }}

/* ---------- 数字涨跌色 ---------- */
.up   {{ color: {UP_RED}; font-weight: 600; }}
.down {{ color: {DOWN_GREEN}; font-weight: 600; }}
.flat {{ color: var(--muted); font-weight: 500; }}

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
    font-size: 0.85rem; color: var(--muted);
    display: flex; gap: 0.7rem; align-items: baseline;
    margin-bottom: 0.25rem;
}}
.round-item .r-head b {{ color: var(--text); font-size: 0.95rem; }}
.round-item p {{ font-size: 0.87rem; line-height: 1.55; margin: 0.15rem 0; }}

/* ---------- 全部股票一览表（颜色全部走主题变量，深浅自动跟随） ---------- */
table.stocks {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.88rem;
    table-layout: auto;
}}
table.stocks th {{
    text-align: left;
    color: var(--muted);
    font-weight: 500;
    padding: 0.6rem 0.7rem;
    border-bottom: 1px solid var(--line);
    background: transparent;
}}
table.stocks td {{
    padding: 0.65rem 0.7rem;
    border-bottom: 1px solid var(--line);
    color: var(--text);
    white-space: normal;
    word-break: break-word;
}}
table.stocks tr:last-child td {{ border-bottom: none; }}
table.stocks td.num {{ font-variant-numeric: tabular-nums; }}

/* ---------- 证据条目 ---------- */
.evidence {{
    border: 1px solid var(--line);
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
    font-size: 0.9rem; font-weight: 500; color: var(--text); line-height: 1.45;
}}
.evidence .ev-meta {{
    font-size: 0.75rem; color: #86868b; white-space: nowrap;
}}
.evidence .ev-snip {{
    font-size: 0.82rem; color: var(--muted); line-height: 1.55;
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
    border: 1px solid var(--line);
    border-radius: 14px;
    background: var(--card-bg);
}}
[data-testid="stExpander"] summary {{
    font-weight: 500; color: var(--text);
}}
.stTabs [data-baseweb="tab-list"] {{ gap: 0; border-bottom: 1px solid #d2d2d7; }}
.stTabs [data-baseweb="tab"] {{
    background: transparent;
    font-size: 0.95rem;
    font-weight: 500;
    color: var(--muted);
    padding: 0.6rem 1.1rem;
    border-radius: 10px 10px 0 0;
}}
.stTabs [aria-selected="true"] {{
    color: {ACCENT_BLUE} !important;
    border-bottom: 2px solid {ACCENT_BLUE} !important;
    background: transparent;
}}
div[data-testid="stPlotlyChart"] {{
    border: 1px solid var(--line);
    border-radius: 18px;
    background: var(--card-bg);
    /* 不能给这里加 padding：Plotly 是按元素 border-box 宽度一次性算好的，
       加了内边距后画布（固定 px）会比内容区宽，出现横向滚动条 */
    padding: 0 !important;
    overflow: hidden;
}}

/* ---------- Agent 报告卡：卡内滚动 + 右上角展开 + 全屏弹窗 ---------- */
/* 隐藏的开关：纯 CSS 控制弹窗开合，不触发 Streamlit 重跑，不污染 URL */
.card-toggle {{ display: none; }}

.card-head {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.8rem;
    margin-bottom: 0.2rem;
}}
.card-head h3 {{ margin: 0; }}
.card-head .card-title {{ margin: 0; }}

.card-expand {{
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    flex-shrink: 0;
    padding: 0.22rem 0.62rem;
    border: 1px solid #d2d2d7;
    border-radius: 999px;
    color: {ACCENT_BLUE};
    font-size: 0.78rem;
    font-weight: 500;
    text-decoration: none !important;
    cursor: pointer;
    transition: background 0.15s ease, border-color 0.15s ease, transform 0.15s ease;
}}
.card-expand:hover {{
    background: rgba(0, 113, 227, 0.06);
    border-color: {ACCENT_BLUE};
    transform: translateY(-1px);
}}

/* 卡片正文：高度封顶 + 内部滚动（细滚动条 + 底部渐隐，暗示"还有内容"） */
.card-scroll {{
    max-height: 300px;
    overflow-y: auto;
    padding-right: 0.45rem;
    scroll-behavior: smooth;
    scrollbar-width: thin;
    scrollbar-color: #d2d2d7 transparent;
    -webkit-mask-image: linear-gradient(to bottom, #000 calc(100% - 26px), rgba(0, 0, 0, 0.12));
    mask-image: linear-gradient(to bottom, #000 calc(100% - 26px), rgba(0, 0, 0, 0.12));
}}
.card-scroll::-webkit-scrollbar {{ width: 6px; }}
.card-scroll::-webkit-scrollbar-track {{ background: transparent; }}
.card-scroll::-webkit-scrollbar-thumb {{
    background: #d2d2d7;
    border-radius: 3px;
}}
.card-scroll::-webkit-scrollbar-thumb:hover {{ background: #b9b9be; }}

/* 弹窗：全屏遮罩 + 背景模糊 + 居中面板 */
.modal {{
    display: none;
    position: fixed;
    inset: 0;
    z-index: 99999;
    align-items: center;
    justify-content: center;
    padding: 2rem 1rem;
}}
.card-toggle:checked ~ .modal {{ display: flex; }}

/* 弹窗打开时锁住背景滚动（点击遮罩的滚轮不再带动整页） */
body:has(.card-toggle:checked) [data-testid="stAppViewContainer"] {{
    overflow: hidden !important;
}}

.modal-backdrop {{
    position: absolute;
    inset: 0;
    background: rgba(28, 28, 30, 0.34);
    backdrop-filter: blur(10px) saturate(120%);
    -webkit-backdrop-filter: blur(10px) saturate(120%);
    cursor: pointer;
}}

.modal-panel {{
    position: relative;
    display: flex;
    flex-direction: column;
    width: min(880px, 94vw);
    max-height: 84vh;
    background: var(--card-bg);
    border-radius: 20px;
    box-shadow: 0 32px 90px rgba(0, 0, 0, 0.28);
    overflow: hidden;
    animation: modal-in 0.18s ease-out;
}}

@keyframes modal-in {{
    from {{ opacity: 0; transform: translateY(10px) scale(0.99); }}
    to   {{ opacity: 1; transform: translateY(0) scale(1); }}
}}

.modal-head {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    padding: 1.05rem 1.4rem;
    border-bottom: 1px solid var(--line);
}}
.modal-title {{
    font-size: 1.05rem;
    font-weight: 600;
    color: var(--text);
    letter-spacing: -0.01em;
}}
.modal-title .modal-sub {{
    margin-left: 0.5rem;
    font-size: 0.8rem;
    font-weight: 400;
    color: var(--muted);
}}

.modal-close {{
    display: flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    flex-shrink: 0;
    border-radius: 50%;
    color: var(--muted);
    font-size: 1.35rem;
    line-height: 1;
    text-decoration: none !important;
    cursor: pointer;
    transition: background 0.15s ease, color 0.15s ease;
}}
.modal-close:hover {{ background: #f5f5f7; color: var(--text); }}

.modal-body {{
    padding: 1.2rem 1.5rem 1.6rem;
    overflow-y: auto;
    scrollbar-width: thin;
    scrollbar-color: #d2d2d7 transparent;
}}
.modal-body::-webkit-scrollbar {{ width: 6px; }}
.modal-body::-webkit-scrollbar-thumb {{ background: #d2d2d7; border-radius: 3px; }}

/* 弹窗内复用卡片的排版，但去掉卡片自身的边框与留白 */
.card.card-flush {{
    border: none;
    background: transparent;
    padding: 0;
    margin: 0;
}}
.card.card-flush h3 {{ display: none; }}

/* ---------- 看板外壳：顶栏 / 侧栏 / 内容宽度 ---------- */
.topbar {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.6rem 1rem;
    flex-wrap: wrap;
    padding: 0.3rem 0 0.9rem;
}}
.tb-brand {{ display: flex; align-items: center; gap: 0.7rem; }}
.tb-logo {{
    width: 38px;
    height: 38px;
    flex: 0 0 38px;
    border-radius: 11px;
    box-shadow: 0 2px 10px rgba(11, 27, 51, 0.18);
}}
.tb-title {{
    font-size: 1.24rem;
    font-weight: 650;
    letter-spacing: -0.02em;
    color: var(--text);
}}
.tb-sub {{
    font-size: 0.78rem;
    color: var(--muted);
    margin-top: 0.16rem;
}}
.tb-right {{ display: flex; gap: 0.45rem; flex-wrap: wrap; }}
.tb-chip {{
    display: inline-flex;
    align-items: baseline;
    gap: 0.28rem;
    padding: 0.28rem 0.7rem;
    border: 1px solid var(--line);
    border-radius: 999px;
    background: var(--card-bg);
    font-size: 0.76rem;
    color: var(--muted);
    white-space: nowrap;
}}
.tb-chip b {{ color: var(--text); font-weight: 600; }}
.tb-chip.good b {{ color: #0a7d32; }}
.tb-chip.warn b {{ color: #9a6700; }}

[data-testid="stSidebar"] {{
    background: #fbfbfd;
    border-right: 0 !important;
    outline: 0 !important;
    box-shadow: 2px 0 14px rgba(16, 24, 40, 0.06) !important;
}}
/* 侧栏从顶栏下沿开始，避免和顶栏重叠 */
section[data-testid="stSidebar"] {{
    top: 58px !important;
    height: calc(100vh - 58px) !important;
    border-right: 0 !important;
}}
/* 导航项做大：整行可点、图标与文字留白，点起来不挤 */
[data-testid="stSidebarNav"] {{ padding: 0.2rem 0.3rem 0; }}
[data-testid="stSidebarNav"] ul {{ display: flex; flex-direction: column; gap: 0.3rem; }}
[data-testid="stSidebarNav"] li a,
[data-testid="stSidebarNav"] a {{
    padding: 0.62rem 0.7rem !important;
    border-radius: 12px !important;
    font-size: 0.95rem !important;
    line-height: 1.3 !important;
    min-height: 2.6rem;
    display: flex;
    align-items: center;
    gap: 0.55rem;
}}
[data-testid="stSidebarNav"] a span {{ font-size: 0.95rem !important; }}
[data-testid="stSidebarNav"] a:hover {{ background: rgba(0, 113, 227, 0.06); }}
[data-testid="stSidebarNav"] a[aria-current="page"] {{
    background: rgba(0, 113, 227, 0.10);
}}
[data-testid="stSidebarNav"] a[aria-current="page"] span {{ font-weight: 600; }}
/* 隐藏 Material 图标字体没加载时泄漏出来的 ligature 文本（侧栏悬停出现的 "keyb..."） */
[data-testid="stSidebarCollapseButton"],
[data-testid="stExpandSidebarButton"],
[data-testid="stSidebarCollapsedControl"],
[data-testid="stToolbar"] {{
    display: none !important;
}}

.block-container,
[data-testid="stMainBlockContainer"] {{
    max-width: 1240px;
    padding-top: 5.4rem;
}}

/* ---------- 通栏固定顶栏 ---------- */
[data-testid="stHeader"] {{ display: none !important; }}
.st-key-topbar {{
    position: fixed;
    inset: 0 0 auto 0;
    z-index: 999999999;
    background: var(--card-bg);
    border-bottom: 1px solid var(--line);
    box-shadow: 0 2px 6px rgba(16, 24, 40, 0.06), 0 10px 30px rgba(16, 24, 40, 0.05);
    padding: 0 1.2rem;
    min-height: 58px;
    display: flex;
    align-items: center;
    backdrop-filter: blur(10px);
}}
.st-key-topbar [data-testid="stVerticalBlock"] {{
    gap: 0 !important;
    min-height: 58px;
    width: 100%;
    justify-content: center;
}}
.st-key-topbar > div, .st-key-topbar [data-testid="stVerticalBlockBorderWrapper"] {{ width: 100%; }}
.st-key-topbar [data-testid="stHorizontalBlock"] {{ align-items: center; }}
.st-key-topbar [data-testid="stHorizontalBlock"] {{ align-items: center; gap: 0.6rem; }}
.st-key-topbar [data-testid="stMarkdownContainer"] p {{ margin: 0; }}
.tb-right {{ display: flex; gap: 0.4rem; flex-wrap: wrap; justify-content: flex-end; }}

/* 输入框右下角的英文提示（Press Enter…）一律不要 */
[data-testid="InputInstructions"] {{ display: none !important; }}

/* 上传组件的中文文案（Streamlit 默认是英文） */
[data-testid="stFileUploaderDropzoneInstructions"] {{ display: none !important; }}
[data-testid="stFileUploaderDropzone"]::before {{
    content: "把图片拖到这里，或点右侧按钮选择文件";
    font-size: 0.8rem;
    color: #86868b;
}}
[data-testid="stFileUploaderDropzone"] button {{ font-size: 0 !important; }}
[data-testid="stFileUploaderDropzone"] button::after {{
    content: "选择图片";
    font-size: 0.82rem;
}}
[data-testid="stFileUploaderDropzone"] small {{ display: none !important; }}

/* 侧栏底部说明：始终贴在侧栏最下方 */
section[data-testid="stSidebar"] > div[data-testid="stSidebarContent"] {{
    display: flex;
    flex-direction: column;
    min-height: calc(100vh - 58px);
    padding-top: 0.7rem;
}}
[data-testid="stSidebarUserContent"] {{ margin-top: auto; }}
.side-foot {{
    padding: 0.75rem 0.35rem 0.9rem;
    border-top: 1px solid var(--line);
    color: #86868b;
    font-size: 0.72rem;
    line-height: 1.7;
}}
.side-foot-line {{ white-space: nowrap; }}

/* Material 图标字体没加载时，图标会退化成英文 ligature 文本（如 expand_more / keyboard…），一律隐藏 */
[data-testid="stIconMaterial"],
span[data-testid="stMaterialIcon"] {{
    display: none !important;
}}
.tb-rule {{
    height: 1px;
    background: #e8e8ed;
    margin: 0.15rem 0 1.1rem;
}}

/* ---------- 模拟持仓 ---------- */
.ptrow {{
    display: flex;
    align-items: center;
    gap: 0.8rem;
    padding: 0.68rem 0.1rem;
    border-bottom: 1px solid #f0f0f3;
}}
.pt-name {{ font-size: 0.95rem; font-weight: 600; min-width: 9rem; }}
.pt-code {{ margin-left: 0.4rem; font-size: 0.72rem; color: #86868b; font-weight: 400; }}
.pt-board {{ min-width: 4.6rem; }}
.pt-cell {{ display: flex; flex-direction: column; min-width: 5.4rem; }}
.pt-cell .pk {{ font-size: 0.68rem; color: #86868b; }}
.pt-cell .pv {{ font-size: 0.92rem; font-weight: 600; font-variant-numeric: tabular-nums; }}
.pt-cell .pv.pos {{ color: #c8102e; }}
.pt-cell .pv.neg {{ color: #0a7d32; }}
.pt-pct {{ min-width: 4.8rem; font-size: 0.9rem; font-weight: 600; }}
.pt-pct.pos {{ color: #c8102e; }}
.pt-pct.neg {{ color: #0a7d32; }}
.pt-days {{ min-width: 4.8rem; font-size: 0.8rem; color: var(--muted); }}
.pt-due {{
    display: inline-block;
    margin-left: 0.3rem;
    padding: 0 0.35rem;
    border-radius: 6px;
    background: #fdecea;
    color: #b3261e;
    font-size: 0.68rem;
}}

/* ---------- 每日建议卡 ---------- */
.pcard {{
    border: 1px solid var(--line);
    border-radius: 18px;
    background: var(--card-bg);
    padding: 1.05rem 1.15rem 1.1rem;
    margin-bottom: 0.9rem;
    transition: box-shadow 0.15s ease, transform 0.15s ease;
}}
.pcard:hover {{
    box-shadow: 0 10px 28px rgba(0, 0, 0, 0.07);
    transform: translateY(-1px);
}}
.pcard-head {{
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 0.6rem;
}}
.pcard-name {{
    font-size: 1.02rem;
    font-weight: 600;
    color: var(--text);
    letter-spacing: -0.01em;
}}
.pcard-code {{
    margin-top: 0.16rem;
    font-size: 0.76rem;
    color: #86868b;
}}
.score-ring {{
    --pct: 0;
    flex-shrink: 0;
    width: 56px;
    height: 56px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    background: conic-gradient(#0a7d32 calc(var(--pct) * 1%), #ececf0 0);
}}
.score-ring span {{
    width: 44px;
    height: 44px;
    border-radius: 50%;
    background: var(--card-bg);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.95rem;
    font-weight: 600;
    color: var(--text);
}}
.pcard-pills {{
    display: flex;
    flex-wrap: wrap;
    gap: 0.32rem;
    margin: 0.72rem 0 0.45rem;
}}
.pcard-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.3rem 0.8rem;
    margin: 0.15rem 0 0.55rem;
}}
.pcard-grid .pk {{
    display: block;
    font-size: 0.71rem;
    color: #86868b;
}}
.pcard-grid .pv {{
    font-size: 0.95rem;
    font-weight: 600;
    color: var(--text);
}}
.pcard-grid .pv.pos {{ color: #c8102e; }}   /* A 股：红涨 */
.pcard-grid .pv.neg {{ color: #0a7d32; }}   /* 绿跌 */
.pcard-reason {{
    font-size: 0.83rem;
    line-height: 1.62;
    color: #424245;
    display: -webkit-box;
    -webkit-line-clamp: 3;
    -webkit-box-orient: vertical;
    overflow: hidden;
}}

/* ---------- 否决区 / 列表行 ---------- */
.prow {{
    display: flex;
    align-items: center;
    gap: 0.8rem;
    padding: 0.55rem 0.2rem;
    border-bottom: 1px solid #f0f0f3;
    font-size: 0.86rem;
}}
.prow:last-child {{ border-bottom: none; }}
.pr-name {{ min-width: 9rem; font-weight: 600; color: var(--text); }}
.pr-code {{ margin-left: 0.3rem; font-size: 0.75rem; font-weight: 400; color: #86868b; }}
.pr-score {{ min-width: 4.6rem; color: var(--muted); }}
.pr-reason {{ flex: 1; color: var(--muted); line-height: 1.55; }}

/* ---------- 股市入门 ---------- */
.gl-def {{ font-size: 0.9rem; line-height: 1.72; color: var(--text); margin: 0 0 0.4rem; }}
.gl-line {{ font-size: 0.84rem; line-height: 1.65; color: #424245; margin: 0.28rem 0; }}
.gl-k {{
    display: inline-block;
    margin-right: 0.42rem;
    padding: 0.04rem 0.4rem;
    border-radius: 6px;
    background: rgba(0, 113, 227, 0.08);
    color: {ACCENT_BLUE};
    font-size: 0.71rem;
}}
.gl-k.bad {{ background: rgba(200, 16, 46, 0.08); color: #c8102e; }}
.disclaimer {{
    text-align: center;
    color: #86868b;
    font-size: 0.75rem;
    line-height: 1.7;
    margin-top: 1.8rem;
}}

/* ---------- 卡片阴影（浅色下也有层次，不是一片死白） ---------- */
.pcard, .metrics, .metric, .card {{
    box-shadow: 0 1px 2px rgba(16, 24, 40, 0.04),
                0 8px 22px rgba(16, 24, 40, 0.06);
}}
.pcard:hover {{
    box-shadow: 0 2px 4px rgba(16, 24, 40, 0.06),
                0 14px 30px rgba(16, 24, 40, 0.10);
    transition: box-shadow 0.18s ease;
}}

/* ---------- 顶栏小按钮 + 品牌文字 ---------- */
.st-key-topbar [data-testid="stBaseButton-secondary"],
.st-key-topbar [data-testid="stPopoverButton"] {{
    min-height: 2rem !important;
    height: 2rem !important;
    padding: 0 0.7rem !important;
    font-size: 0.82rem !important;
    border-radius: 10px !important;
}}
.st-key-topbar [data-testid="stBaseButton-secondary"] p,
.st-key-topbar [data-testid="stPopoverButton"] p {{
    font-size: 0.82rem !important;
    line-height: 1 !important;
}}
.st-key-topbar [data-testid="stHorizontalBlock"] {{ gap: 0.45rem; }}
.tb-brand-text {{ display: flex; flex-direction: column; justify-content: center; }}
.tb-title {{ line-height: 1.12; }}
.tb-sub {{ margin-top: 0 !important; line-height: 1.2; }}

/* 外观弹层：标题 / 分组标题 */
.pop-title {{ font-size: 0.95rem; font-weight: 650; color: var(--text); }}
.pop-label {{
    font-size: 0.78rem;
    color: var(--muted);
    margin: 0.55rem 0 0.15rem;
}}
[data-testid="stPopoverBody"] {{
    border-radius: 18px;
    box-shadow: 0 18px 50px rgba(10, 16, 30, 0.28);
}}

/* ---------- 表格：不撑破卡片，跟随主题 ---------- */
.table-wrap {{
    width: 100%;
    overflow-x: auto;
    border: 1px solid var(--line);
    border-radius: 14px;
    background: var(--card-bg);
}}
.stocks {{
    width: 100%;
    min-width: 0;
    border-collapse: collapse;
    font-size: 0.85rem;
}}
.stocks th {{
    text-align: left;
    font-weight: 500;
    color: var(--muted);
    padding: 0.62rem 0.8rem;
    border-bottom: 1px solid var(--line);
    background: transparent;
    white-space: normal;
}}
.stocks td {{
    padding: 0.58rem 0.8rem;
    color: var(--text);
    border-bottom: 1px solid var(--line);
    white-space: normal;
    word-break: break-word;
}}
.stocks tbody tr:last-child td {{ border-bottom: none; }}
.stocks tbody tr:hover {{ background: rgba(0, 113, 227, 0.04); }}
.stocks td.num {{ font-variant-numeric: tabular-nums; }}
/* Streamlit 原生表格（st.dataframe）也跟随主题、不溢出 */
[data-testid="stDataFrame"], [data-testid="stTable"] {{
    border-radius: 14px;
    overflow: hidden;
    border: 1px solid var(--line);
}}
/* 顶栏右侧按钮：靠右、按内容宽度 */
.st-key-topbar [data-testid="stColumn"]:last-child [data-testid="stHorizontalBlock"] {{
    justify-content: flex-end;
}}
.st-key-topbar [data-testid="stColumn"]:last-child {{ display: flex; justify-content: flex-end; }}
/* 打开外观弹层时，背景轻微模糊（与报告卡弹窗观感一致） */
body:has([data-testid="stPopoverBody"]) [data-testid="stAppViewContainer"] {{
    filter: blur(2px);
    transition: filter 0.18s ease;
}}
/* 顶栏右侧两个按钮：并排贴右、间距 5px */
.st-key-tb_actions {{
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 5px !important;
}}
.st-key-topbar [data-testid="stColumn"] {{
    display: flex;
    align-items: center;
}}
.st-key-tb_actions button {{
    height: 32px !important;
    min-height: 32px !important;
    min-width: 96px !important;
    padding: 0 0.8rem !important;
}}
.st-key-tb_actions [data-testid="stHorizontalBlock"] {{
    gap: 5px !important;
    justify-content: flex-end;
}}
.st-key-tb_actions [data-testid="stPopover"],
.st-key-tb_actions [data-testid="stElementContainer"] {{ margin: 0 !important; }}
/* ---------- Streamlit 原生容器/控件：边框与底色统一跟随主题 ---------- */
[data-testid="stVerticalBlockBorderWrapper"] {{
    border: 0 !important;
    background: transparent !important;
    box-shadow: none !important;
}}
[data-testid="stVerticalBlockBorderWrapper"] > div {{ border: 0 !important; }}
[data-testid="stForm"] {{
    background: var(--card-bg) !important;
    border: 1px solid var(--line) !important;
    border-radius: 14px !important;
}}
[data-testid="stExpander"] {{
    background: var(--card-bg) !important;
    border: 1px solid var(--line) !important;
    border-radius: 14px !important;
}}
[data-testid="stExpander"] summary,
[data-testid="stExpander"] summary p,
[data-testid="stExpander"] details p,
[data-testid="stExpander"] details li {{ color: var(--text) !important; }}
[data-testid="stPlotlyChart"], [data-testid="stPlotlyChart"] > div,
.js-plotly-plot, .plot-container {{ background: transparent !important; }}
[data-baseweb="input"], [data-baseweb="base-input"],
[data-baseweb="select"] > div {{
    background: var(--card-bg) !important;
    color: var(--text) !important;
}}
[data-baseweb="input"] input, [data-baseweb="base-input"] input {{
    color: var(--text) !important;
    background: transparent !important;
}}
[data-testid="stWidgetLabel"] p, [data-testid="stWidgetLabel"] label,
[data-testid="stRadio"] label p, [data-testid="stCheckbox"] label p {{ color: var(--text) !important; }}
[data-testid="stCaptionContainer"] p {{ color: var(--muted) !important; }}
[data-testid="stExpander"] details,
[data-testid="stExpander"] summary {{
    background: transparent !important;
    border: 0 !important;
    box-shadow: none !important;
}}
[data-testid="stExpander"] summary:hover {{ background: rgba(127,127,127,0.08) !important; }}
[data-testid="stExpander"] [data-testid="stExpanderToggleIcon"] svg {{ color: var(--muted) !important; }}
/* 数字输入框（带 -/+ 步进）：底、字、按钮全部跟随主题 */
[data-testid="stNumberInput"] [data-baseweb="input"],
[data-testid="stNumberInput"] input {{
    background: var(--card-bg) !important;
    color: var(--text) !important;
}}
[data-testid="stNumberInput"] button {{
    background: var(--card-bg) !important;
    color: var(--text) !important;
    border-color: var(--line) !important;
}}
[data-testid="stNumberInput"] button:hover {{ background: rgba(127,127,127,0.12) !important; }}
[data-testid="stTextInput"] input, [data-testid="stNumberInput"] input {{
    color: var(--text) !important;
}}
input::placeholder, textarea::placeholder {{ color: var(--muted) !important; opacity: 0.9; }}
/* 品牌区在 58px 顶栏里视觉居中（Streamlit 的 markdown 块自带 8px 偏移） */
.st-key-topbar .tb-brand {{ transform: translateY(-8px); }}
/* ---------- 定时任务卡片：一行显示、垂直居中 ---------- */
.task-card {{
    background: var(--card-bg);
    border: 1px solid var(--line);
    border-radius: 14px;
    overflow: hidden;
    box-shadow: 0 1px 2px rgba(16, 24, 40, 0.04), 0 8px 22px rgba(16, 24, 40, 0.05);
}}
.task-row {{
    display: grid;
    grid-template-columns: 132px minmax(150px, 1fr) 200px 130px;
    align-items: center;
    gap: 1rem;
    padding: 0.72rem 1rem;
    border-bottom: 1px solid var(--line);
}}
.task-row:last-child {{ border-bottom: none; }}
.task-row:hover {{ background: rgba(0, 113, 227, 0.04); }}
.task-row .t-time {{
    font-variant-numeric: tabular-nums;
    font-weight: 600;
    color: var(--text);
    white-space: nowrap;
}}
.task-row .t-name {{ color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.task-row .t-task {{
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 0.78rem;
    color: var(--muted);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}}
.task-row .t-meta {{ color: var(--muted); font-size: 0.82rem; white-space: nowrap; text-align: right; }}
@media (max-width: 1180px) {{
    .task-row {{ grid-template-columns: 110px 1fr 120px; }}
    .task-row .t-task {{ display: none; }}
}}
/* 让两个按钮的「图标 + 文字」整体在按钮里水平居中：
   label 容器默认会撑满剩余宽度，导致内容看起来偏左/偏右；
   fit-content 让它收缩到内容宽度，再交给 button 的 justify-content:center 居中。
   外观按钮里还有一个 20px 的空 chevron 槽（Material 图标被隐藏后留下），一并去掉。 */
.st-key-topbar [data-testid=stPopoverButton] > div,
.st-key-tb_refresh button > div {{
    width: fit-content !important;
    flex: 0 1 auto !important;
}}
.st-key-topbar [data-testid=stPopoverButton] > div > div:nth-child(2) {{
    display: none !important;
}}
/* 刷新按钮：15px 线性循环箭头（mask 取 currentColor，深浅主题自动跟随） */
.st-key-tb_refresh button::before {{
    content: "";
    flex: 0 0 15px;
    display: inline-block;
    width: 15px;
    height: 15px;
    margin-right: 2px;
    vertical-align: -2px;
    background-color: currentColor;
    -webkit-mask: center / 15px 15px no-repeat url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%23000'%3E%3Cpath d='M17.65 6.35A7.958 7.958 0 0 0 12 4c-4.42 0-7.99 3.58-8 8s3.58 8 8 8c3.73 0 6.84-2.55 7.73-6h-2.08A5.99 5.99 0 0 1 12 18c-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z'/%3E%3C/svg%3E");
    mask: center / 15px 15px no-repeat url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%23000'%3E%3Cpath d='M17.65 6.35A7.958 7.958 0 0 0 12 4c-4.42 0-7.99 3.58-8 8s3.58 8 8 8c3.73 0 6.84-2.55 7.73-6h-2.08A5.99 5.99 0 0 1 12 18c-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z'/%3E%3C/svg%3E");
}}

/* ---------------- 降级原因卡片 ---------------- */
.dg-card {{ padding: 0.2rem 0 0.2rem }}
.dg-row {{ display: flex; gap: 0.6rem; align-items: flex-start; padding: 0.7rem 0; border-bottom: 1px solid var(--line) }}
.dg-row:last-child {{ border-bottom: 0 }}
.dg-row .pill {{ flex: 0 0 auto; margin-top: 2px }}
.dg-body {{ display: flex; flex-direction: column; gap: 0.18rem; font-size: 0.9rem; line-height: 1.62; color: var(--text) }}
.dg-stage {{ font-size: 0.78rem; color: var(--muted); letter-spacing: 0.02em }}
.dg-body .muted {{ margin: 0; font-size: 0.82rem }}
.env-block {{ margin-top: 0.75rem; padding-top: 0.7rem; border-top: 1px dashed var(--line); display: flex; flex-direction: column; gap: 0.32rem }}
.env-row {{ display: grid; grid-template-columns: 10px 116px 1fr; align-items: center; gap: 0.55rem; font-size: 0.82rem; color: var(--muted) }}
.env-name {{ color: var(--text) }}
.env-detail {{ font-family: var(--mono, ui-monospace, Menlo, Consolas, monospace); font-size: 0.78rem }}
.dot {{ width: 8px; height: 8px; border-radius: 50%; display: inline-block }}
.dot.ok {{ background: #30b15a }}
.dot.bad {{ background: #e0483d }}
/* ---------------- 顶栏推理模式分段控件 ---------------- */
.st-key-tb_actions [data-testid="stButtonGroup"] button {{
  min-height: 32px; height: 32px; padding: 0 .7rem; font-size: .82rem;
  border-radius: 10px;
}}
.st-key-tb_actions [data-testid="stButtonGroup"] button p {{
  font-size: .82rem; font-weight: 500;
}}
.st-key-tb_actions [data-testid="stButtonGroup"][aria-checked="true"] {{
  background: var(--card-bg);
}}
/* 顶栏右侧一行排布，不允许换行 */
.st-key-tb_actions {{ flex-wrap: nowrap !important; align-items: center; }}
.st-key-tb_actions > div {{ flex: 0 0 auto; }}
/* 顶栏推理模式开关：压到 32px 高、与按钮同高 */
.st-key-tb_mode_open {{ display: flex; align-items: center; height: 32px; padding-right: .2rem }}
.st-key-tb_mode_open [data-testid="stWidgetLabel"] p {{ font-size: .82rem; font-weight: 500 }}
.st-key-tb_mode_open [data-testid="stCheckbox"] {{ height: 32px; display: flex; align-items: center }}
/* 外观弹层：浮到页面正中（原来贴着按钮往下弹，顶部会被固定顶栏压住） */
div[data-baseweb="popover"] {{
  position: fixed !important; left: 50% !important; top: 50% !important;
  transform: translate(-50%, -50%) !important; margin: 0 !important;
  width: min(560px, calc(100vw - 40px)) !important; max-width: 560px !important;
}}
div[data-baseweb="popover"] [data-testid="stPopoverBody"] {{
  width: 100% !important; max-height: 76vh; overflow: auto;
}}
/* 控件提示气泡必须盖过固定顶栏（顶栏 z-index 999999999），否则被切成半截 */
div:has(> div[data-baseweb="tooltip"]) {{ z-index: 2147483600 !important; }}
div[data-baseweb="tooltip"] {{ z-index: 2147483600 !important; }}
/* 外观弹层：居中面板的最终尺寸（覆盖前面的旧规则） */
div[data-baseweb="popover"] {{
  width: min(600px, calc(100vw - 40px)) !important;
  max-width: 600px !important;
  max-height: 88vh !important;
}}
div[data-baseweb="popover"] [data-testid="stPopoverBody"] {{
  max-height: calc(88vh - 24px) !important;
  overflow: auto !important;
  padding: 1.05rem 1.15rem 0.85rem !important;
}}
/* 外观弹窗（st.dialog）垂直居中 */
[data-testid="stDialog"] {{
  display: flex !important;
  align-items: center !important;
  justify-content: center !important;
}}
[data-testid="stDialog"] > div {{ max-height: 90vh; }}
[data-testid="stDialog"] [role="dialog"] {{
  margin: 0 !important;
  max-height: 88vh;
  overflow: auto;
}}
/* 外观弹窗（st.dialog）：固定居中，覆盖 Streamlit 默认的贴顶布局 */
[data-testid="stDialog"] [role="dialog"] {{
  position: fixed !important;
  left: 50% !important;
  top: 50% !important;
  transform: translate(-50%, -50%) !important;
  margin: 0 !important;
  width: min(560px, calc(100vw - 32px)) !important;
  max-height: 88vh !important;
  overflow: auto !important;
}}
/* 提示气泡：把整条祖先链的层叠上下文抬到顶栏之上（顶栏 999999999） */
div:has(div[data-baseweb="tooltip"]) {{ z-index: 2147483600 !important; }}
div[data-baseweb="tooltip"] {{ position: fixed !important; z-index: 2147483600 !important; }}

/* 大模型档位 eco / max：比顶栏按钮小一号 */
.st-key-tb_llm_tier {{ gap: 4px !important; align-items: center; }}
.st-key-tb_llm_tier [data-testid="stButtonGroup"] {{ gap: 4px !important; }}
.st-key-tb_llm_tier button {{
  min-width: 54px !important; width: auto !important;
  height: 26px !important; min-height: 26px !important;
  padding: 0 0.55rem !important;
  font-size: 0.74rem !important; letter-spacing: 0.02em;
  border-radius: 8px !important;
}}
.st-key-tb_llm_tier button p {{ font-size: 0.74rem !important; }}
</style>
"""


def inject_apple_style() -> None:
    """注入全局苹果风格 CSS（每个 Streamlit 会话调用一次即可）。"""
    st.markdown(_CSS, unsafe_allow_html=True)








