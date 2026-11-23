# -*- coding: utf-8 -*-
"""main.py — Streamlit 看板外壳（顶栏 + 左侧导航 + 内容卡片）

页面结构：
    每日建议  —— 今日建议入手 / 观察名单 / 否决区（读 data/signals）
    个股分析  —— 批次概览、K 线、情感趋势、Agent 报告、证据链、迭代闭环
    模拟持仓  —— 记一笔模拟买入，按期限用真实收盘价结算盈亏
    股市入门  —— 术语与黑话词条库（读 data/knowledge/glossary.json）
    数据更新  —— 网页内触发增量入库与重算推荐（DuckDB 单写者，更新时让出库）

另外两件事在 ``lifecycle`` 里：
    - 页面关闭 → 本机信标 → 后端进程 8 秒内自动退出（不留孤儿 Streamlit）
    - 关掉页面就不会继续占着 8501 端口

运行（项目根目录）:
    streamlit run src/app/main.py
或双击桌面的「毕设看板」快捷方式。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 减少 CUDA 显存碎片：必须在 import torch 之前设置（Streamlit 会在页面里再 import torch）
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from src.app import layout  # noqa: E402
from src.app.lifecycle import page_beacon, start as start_lifecycle  # noqa: E402
from src.app.pages import (  # noqa: E402
    daily_picks,
    data_ops,
    learn,
    paper_trade,
    stock_analysis,
)
from src.app.style import inject_apple_style  # noqa: E402

st.set_page_config(
    page_title="A股舆情与量化策略看板",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 注意顺序：``st.navigation`` 必须尽早声明（紧随 set_page_config），
# 否则直接访问 /stock、/paper 这类深链接时会先弹一次 "Page not found" 再纠正。
nav = st.navigation(
    [
        st.Page(daily_picks.render, title="每日建议",
                icon="🎯", default=True),
        st.Page(stock_analysis.render, title="个股分析",
                icon="📈", url_path="stock"),
        st.Page(paper_trade.render, title="模拟持仓",
                icon="💼", url_path="paper"),
        st.Page(learn.render, title="股市入门",
                icon="📚", url_path="learn"),
        st.Page(data_ops.render, title="数据更新",
                icon="🔄", url_path="ops"),
    ],
    position="sidebar",
)

inject_apple_style()
layout.init_state()
layout.apply_theme()

# 1) 生命周期：网页关闭 → 后端退出；2) 把心跳脚本挂进页面
start_lifecycle()
page_beacon()

# 3) 上方顶栏 + 侧栏页脚 + 页面内容
layout.topbar()
layout.sidebar_foot()
nav.run()
