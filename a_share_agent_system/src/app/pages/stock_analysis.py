# -*- coding: utf-8 -*-
"""个股分析页：K 线 + 情感趋势 + 三 Agent 报告 + 证据链 + 迭代闭环。

两种数据来源：
1. **批次评测里的股票** —— 读 ``data/processed/batch_eval_*.json``（Phase 3.2 产物），
   带批次统计与「全部股票一览」表；
2. **任意股票代码** —— 输入 6 位代码现场跑一遍 Analyst→Strategist→Critic，
   结果只做会话内缓存（``src/app/live.py``，1 小时），不写评测集。
"""

from __future__ import annotations

import streamlit as st

from src.agents import degrade
from src.app import charts, data, layout, live, llm_status, views

MODE_BATCH = "批次评测里的股票"
MODE_CUSTOM = "任意股票代码"


# ---------------------------------------------------------------- 控件
def _batch_controls() -> dict | None:
    batches = data.list_batch_files()
    if not batches:
        st.warning("未找到 batch_eval_*.json，请先运行 `scripts/batch_eval.py`。")
        return None

    labels = {
        b["name"]: f"{b['name'].replace('batch_eval_', '')}（"
        f"{len(data.load_batch(b['path'])['results'])} 只）"
        for b in batches
    }
    c1, c2, c3 = st.columns([1.2, 1.6, 1], gap="medium", vertical_alignment="bottom")
    with c1:
        picked = st.selectbox(
            "评测批次", list(labels.keys()),
            format_func=lambda k: labels.get(k, k), index=0, key="ctl_batch",
        )
    batch_path = next(b["path"] for b in batches if b["name"] == picked)
    batch = data.load_batch(batch_path)
    results = batch["results"]

    options = [
        f"{r['stock_code']}　{data.name_of(r['stock_code'], batch['meta'])}"
        for r in results
    ]
    with c2:
        idx = st.selectbox(
            "股票", range(len(options)),
            format_func=lambda i: options[i], index=0, key="ctl_stock",
        )
    with c3:
        n_bars = st.slider("K 线根数", 60, 250, 120, step=10, key="ctl_bars")

    return {
        "mode": MODE_BATCH,
        "batch": batch,
        "code": results[idx]["stock_code"],
        "n_bars": n_bars,
    }


def _custom_controls() -> dict | None:
    c1, c2, c3 = st.columns([1.35, 1.5, 1], gap="medium", vertical_alignment="bottom")
    with c1:
        raw = st.text_input(
            "股票代码",
            value=st.session_state.get("custom_code", "600519"),
            max_chars=6,
            placeholder="600519 / 000001",
            key="ctl_code",
        )
    with c2:
        is_live = st.checkbox("现场抓当天新闻（约 2–3 秒）", value=True, key="ctl_live")
    with c3:
        n_bars = st.slider("K 线根数", 60, 250, 120, step=10, key="ctl_bars_c")

    six = data.pretty_code(raw)
    if len(six) != 6 or not six.isdigit():
        st.caption(
            "输入 6 位代码后自动分析：600519 贵州茅台 / 000001 平安银行 / "
            "300750 宁德时代 / 688981 中芯国际。"
        )
        return None
    if not data.has_code(six):
        st.warning(f"库里没有 {six} 的行情数据，可能已退市或代码有误。")
        return None

    st.session_state["custom_code"] = six
    use_llm = st.session_state.get("ui_mode", "rules") == "llm"

    # 确认后才分析：避免每敲一次代码就重跑一遍（尤其开了大模型时要几十秒）
    c_btn, c_tip = st.columns([1, 3.4], gap="medium", vertical_alignment="center")
    with c_btn:
        go = st.button("开始分析", key="ctl_go", type="primary", use_container_width=True)
    sig = (six, use_llm, bool(is_live), st.session_state.get("ui_tier", "max") if use_llm else "rules")
    if go:
        st.session_state["_run_sig"] = sig
        st.session_state.pop("_run_at", None)
    with c_tip:
        st.caption(
            f"数据截止日 {data.latest_trade_date()} · 改完代码/档位后点「开始分析」才跑；"
            "结果只在本次会话生效，不写入评测集（要留存请跑 `scripts/ask_stock.py`）。"
        )
    return {
        "mode": MODE_CUSTOM, "code": six, "n_bars": n_bars, "live": is_live,
        "use_llm": use_llm, "sig": sig,
    }


def _controls() -> dict | None:
    mode = st.radio(
        "分析对象", [MODE_BATCH, MODE_CUSTOM], horizontal=True,
        key="ctl_mode", label_visibility="collapsed",
    )
    picked = _batch_controls() if mode == MODE_BATCH else _custom_controls()
    if picked:
        st.session_state["selected_code"] = picked["code"]
    return picked


# ---------------------------------------------------------------- 批次概览
def _batch_overview(meta: dict, stats: dict, results: list[dict], as_of: str) -> None:
    layout.page_head("批次概览", f"数据截止日 {as_of}")
    wall_s = (meta.get("wall_clock_ms") or 0) / 1000
    layout.md(
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
    layout.md(
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


# ---------------------------------------------------------------- 主流程
def render() -> None:
    if layout.busy_guard():
        return

    ctl = _controls()
    if ctl is None:
        return

    code = ctl["code"]
    n_bars = ctl["n_bars"]
    results: list[dict] | None = None

    if ctl["mode"] == MODE_BATCH:
        batch = ctl["batch"]
        meta, stats, results = batch["meta"], batch["stats"], batch["results"]
        result = next(r for r in results if r["stock_code"] == code)
        as_of = str(
            meta.get("as_of")
            or (result.get("report") or {}).get("as_of")
            or data.latest_trade_date()
        )[:10]
        mode_llm = False
        _batch_overview(meta, stats, results, as_of)
    else:
        as_of = data.latest_trade_date()
        mode_llm = bool(ctl.get("use_llm"))
        tier = st.session_state.get("ui_tier", "max") if mode_llm else "rules"
        if st.session_state.get("_run_sig") != ctl.get("sig"):
            st.info(
                "确认代码与模式无误后，点上方 **「开始分析」** 才会运行"
                + ("（大模型档位需要几十秒到两分钟）" if mode_llm else "（规则模式约 1–2 秒）")
                + "。"
            )
            return
        with st.spinner(
            f"正在分析 {code}（Analyst → Strategist → Critic"
            + ("，经济档：仅 Analyst 走大模型）" if mode_llm and tier == "eco"
               else ("，三层均走大模型）" if mode_llm else "）"))
        ):
            result = live.analyze(
                code, as_of, ctl["live"], "llm" if mode_llm else "rules", tier
            )
        # 「分析完自动释放显存」：勾上后每次都把模型卸掉（下次重载约 14 秒）
        if mode_llm and st.session_state.get("ui_auto_free"):
            _freed = live.release_llm()
            if _freed:
                st.toast(f"已自动释放显存 {_freed} GB")
        if mode_llm and result.get("error"):
            st.warning(
                "大模型模式加载失败，已回退规则路径：" + str(result["error"])
                + "　—— 装上 transformers / bitsandbytes / accelerate / peft 后重试。"
            )
            result.pop("error", None)
            result["_mode"] = "rules"
            mode_llm = False
        if result.get("error"):
            st.error("分析失败：" + str(result["error"]))
            return
        meta = {
            "as_of": as_of,
            "names": {},
            "backend": (
                "qwen（经济档）" if mode_llm and tier == "eco"
                else ("qwen" if mode_llm else "规则引擎（未接大模型）")
            ),
        }
        layout.page_head(
            "个股速览",
            f"数据截止日 {as_of} · 即时分析（Analyst 舆情 → Strategist 策略 → Critic 校验）",
        )

    rep = result.get("report") or {}
    adv = result.get("advice") or {}
    verdict = result.get("verdict") or {}

    # ------------------------------------------------------------ 当前股票
    name = data.name_of(code, meta)
    layout.page_head(f"{code}　{name}" if name else code)
    layout.md(views.pill_row_summary(rep, adv, verdict))

    # ---------------------------------------------------------- 降级原因
    dg_rows = degrade.explain(
        result, llm_attached=mode_llm,
        tier=tier if mode_llm else "max",
    )
    has_degrade = any(r["level"] in ("规则", "降级") for r in dg_rows)
    layout.page_head(
        "为什么走规则路径" if has_degrade else "本次分析链路（未降级）",
        "每次分析都会记录三个 Agent 是否用上大模型、证据链是否退化、编排层是否兜底降级。",
    )
    env = llm_status.probe()
    layout.md(views.degrade_card(dg_rows, env["checks"]))
    if mode_llm and env["blockers"]:
        st.warning(
            "大模型模式已开启，但环境还缺：" + "、".join(env["blockers"])
            + "。装好依赖即可自动生效，命令：pip install transformers bitsandbytes accelerate peft。"
        )
    with st.expander(
        "环境体检明细 / 怎么真正接上大模型"
        + ("（当前阻断：" + "、".join(env["blockers"]) + "）" if env["blockers"] else ""),
        expanded=False,
    ):
        for item in env["checks"]:
            st.write(("✅" if item["ok"] else "❌"), f"**{item['name']}**", "—", item["detail"])
        st.caption(llm_status.INSTALL_HINT)

        # 显存面板：看当前占用、手动释放（长时间不用时把卡还回去）
        _vi = live.vram()
        if _vi:
            st.caption(
                f"显存：模型占用 **{_vi['allocated']} GB**（缓存池 {_vi['reserved']} GB）"
                f" · 空闲 {_vi['free']} / {_vi['total']} GB"
            )
        c_rel, c_tip = st.columns([1, 3], vertical_alignment="center")
        st.checkbox(
            "分析完自动释放显存（下次分析要重新加载，约 14 秒）",
            key="ui_auto_free",
            value=False,
        )
        with c_rel:
            if st.button("释放模型显存", key="release_vram", use_container_width=True):
                freed = live.release_llm()
                if freed:
                    st.toast(f"已释放 {freed} GB 显存")
                else:
                    st.toast("模型本来就没加载，显存已清空")
                st.rerun()
        with c_tip:
            st.caption("释放后下次分析会重新加载（约 14 秒）；切回「规则」模式也会自动释放。")

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

    sdf = data.load_sentiment_daily(code, as_of)
    if not sdf.empty:
        st.plotly_chart(
            charts.sentiment_chart(sdf), width="stretch",
            config={"displayModeBar": False},
        )
    else:
        st.info("该股票暂无词典情感标签数据。")

    # ------------------------------------------------------------ Agent 报告
    layout.page_head(
        "多智能体分析报告",
        "Analyst 舆情研判 → Strategist 策略生成 → Critic 质量校验，含完整证据链与迭代闭环记录。",
    )
    left, mid, right = st.columns(3)
    with left:
        layout.md(views.analyst_view(rep))
    with mid:
        layout.md(views.strategist_view(adv))
    with right:
        layout.md(views.critic_view(verdict))

    col_a, col_b = st.columns(2)
    with col_a:
        layout.md(views.market_snapshot(rep.get("market_snapshot") or {}))
    with col_b:
        fin = rep.get("financial_snapshot") or {}
        layout.md(
            views.financial_snapshot(fin) if fin.get("report_date")
            else views.empty_financial_card()
        )

    # ------------------------------------------------------------ 证据链 + 迭代
    layout.page_head("证据链", "RAG 语义检索 + 词典打标，逐条可溯源。")
    with st.expander(
        f"检索证据 {len(rep.get('evidence') or [])} 条（点击展开）", expanded=False
    ):
        layout.md(views.evidence_list(rep.get("evidence") or []))

    layout.page_head(
        "迭代闭环",
        "Critic 不通过 → 反馈注入 Strategist → 温度衰减重试，连续两轮问题相同则提前终止。",
    )
    layout.md(views.rounds_timeline(result.get("rounds") or []))

    # ------------------------------------------------------------ 全部股票（仅批次）
    if results:
        layout.page_head("全部股票一览")
        layout.md(views.stocks_table(results, data.stock_names()))

    layout.footer(meta)
