# -*- coding: utf-8 -*-
"""live.py — 任意股票的即时分析（不依赖 batch_eval 批次文件）。

前端「个股分析」页除了看历史评测批次，还允许输入任意 6 位代码现场跑一遍
Analyst → Strategist → Critic，方便临时查一只没跑过批量的股票。

设计要点：
1. 结果只在会话内缓存 1 小时，**不写 data/processed**，避免污染评测集；
2. 分析对象是「截止某一交易日」的历史快照，prompt 与批次评测完全一致；
3. 现场抓新闻（L2）是可选项：关掉就只用本地已入库的新闻 + 语义检索；
4. 推理模式可选：``rules`` 走规则（1–2 秒），``llm`` 接 Qwen2.5-7B 4bit
   （首次加载约 40 秒、占 5–6 GB 显存）；加载失败时回退规则并说明原因；
5. 任何异常都收敛成 ``{"error": ...}`` 返回，前端显示错误而不是整页崩掉。
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from src.app import data


@st.cache_resource(show_spinner="首次加载 Qwen2.5-7B（4bit），大约 40 秒…")
def load_llm() -> Any:
    """加载并缓存 Qwen 后端（整个会话只加载一次）。"""
    from src.models.llm import get_backend

    return get_backend("qwen")


def vram() -> dict:
    """当前显存（GB）：allocated / reserved / free / total。"""
    from src.models.llm.qwen import vram_info

    return vram_info()


def release_llm() -> float:
    """卸载大模型并清空显存，返回释放的 GB 数。"""
    before = vram().get("allocated", 0.0)
    try:
        backend = load_llm()
        if hasattr(backend, "unload"):
            backend.unload()
    except Exception:  # noqa: BLE001 —— 没加载过也算成功
        pass
    try:
        load_llm.clear()  # 清掉 cache_resource，下次用会重新加载
    except Exception:  # pragma: no cover
        pass
    from src.models.llm.qwen import _trim_vram

    _trim_vram()
    after = vram().get("allocated", 0.0)
    return round(max(0.0, before - after), 2)


def llm_error() -> str | None:
    """尝试加载模型，返回错误说明（None 表示可用）。不抛异常。"""
    try:
        load_llm()
        return None
    except Exception as exc:  # noqa: BLE001 —— 前端要展示原因
        return f"{type(exc).__name__}: {exc}"


@st.cache_data(show_spinner=False, ttl=3600)
def analyze(
    code: str,
    as_of: str,
    live_news: bool = True,
    mode: str = "rules",
    tier: str = "max",
) -> dict[str, Any]:
    """对单只股票跑一遍多智能体流水线，返回与批次结果同构的 dict。

    Args:
        code: 6 位股票代码或 sh./sz. 前缀代码。
        as_of: 数据截止日（YYYY-MM-DD），Point-in-Time 边界。
        live_news: 是否现场抓当天新闻（缓存 6 小时，见 news_live）。
        mode: ``rules``（纯规则）或 ``llm``（接 Qwen 后端）。
        tier: 大模型档位，``max`` 三层都用、``eco`` 只有 Analyst 用。

    Returns:
        ``PipelineResult.to_json_dict()`` 的结果，额外带 ``stock_code``、
        ``_live=True``、``_mode``；失败时返回 ``{"error": ...}``。
    """
    six = data.pretty_code(code)
    try:
        from src.agents.orchestrator import Orchestrator

        llm = None
        if mode == "llm":
            llm = load_llm()  # 失败会抛异常，被下面的 except 收住
        orchestrator = Orchestrator(
            llm=llm, live_news=bool(live_news),
            llm_scope="all" if tier != "eco" else "analyst",
        )
        payload = orchestrator.run({"stock_code": six, "as_of": as_of})
        result = payload["result"]
        out = result.to_json_dict()
        out["stock_code"] = six
        out["_live"] = True
        out["_mode"] = mode
        out["_tier"] = tier
        return out
    except Exception as exc:  # noqa: BLE001 —— 前端需要看到原因
        return {
            "stock_code": six,
            "error": f"{type(exc).__name__}: {exc}",
            "_mode": mode,
            "_tier": tier,
        }
    finally:
        # 每次分析都把临时显存还回去，避免连跑几次后越占越多
        from src.models.llm.qwen import _trim_vram

        _trim_vram()
