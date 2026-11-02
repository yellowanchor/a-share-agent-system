# -*- coding: utf-8 -*-
"""main.py — Streamlit 前端看板主入口（Phase 3.3）

苹果官网简约风格的多智能体分析看板，读取 Phase 3.2 批量评测产物，
叠加 DuckDB 行情与词典情感标签，实现证据可溯源的可视化：

    1. 批次概览   —— 首轮/终局通过率、闭环增益、耗时
    2. K 线图     —— 前复权日线 + 均线 + 策略价位线 + PIT 截止日
    3. 情感趋势   —— 日频利多/利空 + 7 日净情绪均线（词典打标）
    4. Agent 报告 —— Analyst/Strategist/Critic 三卡片 + 证据链 + 迭代闭环时间线

运行（项目根目录）:
    streamlit run src/app/main.py

本看板为纯只读展示层，不加载 LLM / 不触发推理，任何环境装上
streamlit + plotly + duckdb + pandas 即可运行。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 支持直接 streamlit run src/app/main.py（无需先 pip install -e .）
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from src.app import charts, data, views  # noqa: E402
from src.app.style import inject_apple_style  # noqa: E402

# ---------------------------------------------------------------- page conf
st.set_page_config(
    page_title="A股舆情与量化策略看板",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)
inject_apple_style()


def md(html_str: str) -> None:
    """统一渲染 HTML 片段（unsafe_allow_html 由这里集中管理）。

    注意：st.markdown 会把「行首 ≥4 空格缩进的行」解析成 markdown 代码块，
    导致 HTML 标签以原文形式泄漏，所以这里逐行 strip 并剔除空行。
    """
    cleaned = "\n".join(
        line.strip() for line in html_str.splitlines() if line.strip()
    )
    st.markdown(cleaned, unsafe_allow_html=True)


# ---------------------------------------------------------------- sidebar
def render_sidebar() -> tuple[str, str, dict, int] | None:
    """批次 + 股票选择器。Returns: (batch_path, stock_code, batch, n_bars)"""
    st.markdown("### 看板控制")
    batches = data.list_batch_files()
    if not batches:
        st.warning("未找到 batch_eval_*.json，请先运行 scripts/batch_eval.py")
        return None

    labels = {
        b["name"]: f"{b['name'].replace('batch_eval_', '')}（"
        f"{len(data.load_batch(b['path'])['results'])} 只）"
        for b in batches
    }
    picked = st.selectbox(
        "评测批次",
        list(labels.keys()),
        format_func=lambda k: labels.get(k, k),
        index=0,
    )
    batch_path = next(b["path"] for b in batches if b["name"] == picked)
    batch = data.load_batch(batch_path)

    results = batch["results"]
    options = [
        f"{r['stock_code']}　{data.name_of(r['stock_code'], batch['meta'])}"
        for r in results
    ]
    idx = st.selectbox("股票", range(len(options)),
                       format_func=lambda i: options[i], index=0)
    code = results[idx]["stock_code"]

    st.divider()
    st.markdown("**图表选项**")
    n_bars = st.slider("K 线根数", 60, 250, 120, step=10)
    return batch_path, code, batch, n_bars


def main() -> None:
    # ---------------------------------------------------------------- hero
    md(
        views.hero(
            "A股舆情与量化策略看板",
            "千问大模型多智能体 · 证据可溯源的舆情研判、策略生成与质量校验",
            [("架构", "Analyst → Strategist → Critic"),
             ("风格", "苹果官网简约风")],
        )
    )

    sidebar_out = render_sidebar()
    if sidebar_out is None:
        return
    _, code, batch, n_bars = sidebar_out
    meta, stats, results = batch["meta"], batch["stats"], batch["results"]
    result = next(r for r in results if r["stock_code"] == code)
    rep = result.get("report") or {}
    adv = result.get("advice") or {}
    verdict = result.get("verdict") or {}
    as_of = str(meta.get("as_of") or rep.get("as_of") or "2026-08-20")[:10]

    # ------------------------------------------------------------ 概览
    md(views.section("批次概览", f"数据截止日 {as_of}"))
    wall_s = (meta.get("wall_clock_ms") or 0) / 1000
    md(
        views.metrics_row([
            (f"{stats.get('n_stocks', 0)}", "评测股票数"),
            (f"{(stats.get('first_pass_rate') or 0) * 100:.0f}"
             f"<span class='unit'>%</span>", "首轮通过率"),
            (f"{(stats.get('final_pass_rate') or 0) * 100:.0f}"
             f"<span class='unit'>%</span>", "终局通过率"),
            (f"+{(stats.get('retry_gain') or 0) * 100:.0f}"
             f"<span class='unit'>pt</span>", "闭环增益"),
            (f"{stats.get('avg_rounds', 1):.1f}", "平均迭代轮数"),
            (f"{wall_s / 60:.1f}<span class='unit'>min</span>", "整批耗时"),
        ])
    )
    md(
        views.metrics_row([
            (f"{stats.get('avg_evidence', 0):.1f}", "平均证据条数"),
            (f"{stats.get('avg_position_pct', 0):.1f}<span class='unit'>%</span>",
             "平均建议仓位"),
            (str(meta.get("backend", "—")), "推理后端"),
            (f"{meta.get('max_retries', 0)}", "最大重试次数"),
            (f"{'开' if meta.get('llm_critic') else '关'}", "LLM 语义校验"),
        ])
    )

    st.plotly_chart(
        charts.batch_pass_chart(results), width="stretch",
        config={"displayModeBar": False},
    )

    # ------------------------------------------------------------ 当前股票
    name = data.name_of(code, meta)
    md(
        views.section(
            f"{code}　{name}" if name else code,
            "",
        )
    )
    md(views.pill_row_summary(rep, adv, verdict))

    # ------------------------------------------------------------ K 线 + 情感
    kdf = data.load_kline(code, as_of, n_bars)
    if not kdf.empty:
        st.plotly_chart(
            charts.kline_chart(
                kdf, as_of, advice=adv, market=rep.get("market_snapshot") or None
            ),
            width="stretch", config={"displayModeBar": False},
        )
    else:
        st.info("该股票在 daily_hfq 中无前复权行情数据。")

    sdf = data.load_sentiment_daily(code)
    if not sdf.empty:
        st.plotly_chart(
            charts.sentiment_chart(sdf), width="stretch",
            config={"displayModeBar": False},
        )
    else:
        st.info("该股票暂无词典情感标签数据。")

    # ------------------------------------------------------------ Agent 报告
    md(views.section(
        "多智能体分析报告",
        "Analyst 舆情研判 → Strategist 策略生成 → Critic 质量校验，"
        "含完整证据链与迭代闭环记录。",
    ))
    left, mid, right = st.columns(3)
    with left:
        md(views.analyst_view(rep))
    with mid:
        md(views.strategist_view(adv))
    with right:
        md(views.critic_view(verdict))

    col_a, col_b = st.columns(2)
    with col_a:
        md(views.market_snapshot(rep.get("market_snapshot") or {}))
    with col_b:
        fin = rep.get("financial_snapshot") or {}
        md(
            views.financial_snapshot(fin) if fin.get("report_date")
            else views.empty_financial_card()
        )

    # ------------------------------------------------------------ 证据链 + 迭代
    md(views.section("证据链", "RAG 语义检索 + 词典打标，逐条可溯源。"))
    with st.expander(
        f"检索证据 {len(rep.get('evidence') or [])} 条（点击展开）", expanded=False
    ):
        md(views.evidence_list(rep.get("evidence") or []))

    md(views.section(
        "迭代闭环",
        "Critic 不通过 → 反馈注入 Strategist → 温度衰减重试，"
        "连续两轮问题相同则提前终止。",
    ))
    md(views.rounds_timeline(result.get("rounds") or []))

    # ------------------------------------------------------------ 全部股票
    md(views.section("全部股票一览"))
    md(views.stocks_table(results, data.stock_names()))

    st.markdown(
        '<p style="text-align:center;color:#86868b;font-size:0.75rem;'
        'margin-top:2.5rem">基于千问大模型多智能体的A股舆情分析与量化策略系统'
        f" · 批次 {views.esc(meta.get('generated_at', ''))} · "
        "仅用于学术研究</p>",
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
