# -*- coding: utf-8 -*-
"""股市入门页：常出现的术语 / 黑话解释（词条来自 data/knowledge/glossary.json）。

交互：关键词搜索 + 分类筛选（st.pills）+ 折叠卡（展开看定义 / 例子 / 坑）。
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from src.app import data, layout, views

_LEVEL_CN = {"beginner": "入门", "basic": "基础", "intermediate": "进阶"}


def _entry_md(e: dict[str, Any]) -> str:
    aliases = e.get("aliases") or []
    alias_txt = f"（也叫 {'、'.join(aliases)}）" if aliases else ""
    return f"**{e.get('term', '')}**{alias_txt}"


def render() -> None:
    book = data.glossary()
    entries = book.get("entries") or []
    if not entries:
        st.info("词条库为空：`data/knowledge/glossary.json` 不存在或未生成。")
        return

    cats = ["全部"] + sorted({str(e.get("category") or "其他") for e in entries})
    layout.page_head(
        "股市入门",
        f"{len(entries)} 条常见术语与黑话 · 用大白话讲清「是什么、怎么用、哪里会亏钱」",
    )

    cat = st.pills("分类", cats, default="全部", key="glossary_cat")

    # 搜索放表单里：回车与点「搜索」都提交，框内不再出现 "Press Enter to apply"
    with st.form("glossary_search", border=False):
        c1, c2 = st.columns([4.2, 1], gap="medium",
                            vertical_alignment="bottom")
        with c1:
            kw = st.text_input(
                "搜索术语", placeholder="例如：打板、换手率、市盈率、洗盘",
                key="glossary_kw", label_visibility="collapsed",
            )
        with c2:
            st.form_submit_button("🔍 搜索", type="primary",
                                  use_container_width=True)

    picked = [e for e in entries if cat in (None, "全部") or e.get("category") == cat]
    if kw.strip():
        k = kw.strip().lower()
        picked = [
            e for e in picked
            if k in str(e.get("term", "")).lower()
            or any(k in str(a).lower() for a in (e.get("aliases") or []))
            or k in str(e.get("definition", "")).lower()
        ]

    st.caption(f"命中 {len(picked)} 条")
    if not picked:
        st.info("没有匹配的词条，换个关键词试试。")
        return

    left, right = st.columns(2, gap="medium")
    for i, e in enumerate(picked):
        with (left if i % 2 == 0 else right):
            level = _LEVEL_CN.get(str(e.get("level") or ""), str(e.get("level") or ""))
            tag = f"[{e.get('category', '')}{(' · ' + level) if level else ''}]"
            with st.expander(f"{e.get('term', '')}　{tag}", expanded=(len(picked) <= 3)):
                layout.md(
                    f'<p class="gl-def">{views.esc(e.get("definition") or "")}</p>'
                )
                if e.get("example"):
                    layout.md(
                        f'<p class="gl-line"><span class="gl-k">举个例子</span>'
                        f'{views.esc(e["example"])}</p>'
                    )
                if e.get("pitfall"):
                    layout.md(
                        f'<p class="gl-line"><span class="gl-k bad">小心的坑</span>'
                        f'{views.esc(e["pitfall"])}</p>'
                    )
                if e.get("related"):
                    layout.md(
                        '<p class="gl-line"><span class="gl-k">相关</span>'
                        + "、".join(views.esc(r) for r in e["related"])
                        + "</p>"
                    )

    layout.md(
        f'<p class="disclaimer">{views.esc(book.get("disclaimer") or "")}</p>'
    )
