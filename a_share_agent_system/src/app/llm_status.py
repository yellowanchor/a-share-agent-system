# -*- coding: utf-8 -*-
"""llm_status.py — 大模型可用性体检（前端「为什么走规则路径」用）。

不加载模型、不占显存，只做静态体检：
1. 基座权重目录是否存在、权重文件合计多大；
2. CUDA / 显存；
3. `transformers` / `bitsandbytes` / `peft` / `accelerate` 是否装齐
   （4bit 量化推理必须前两个）。

结果缓存 5 分钟，足够反映"刚装完依赖"的状态。
"""

from __future__ import annotations

from typing import Any

import streamlit as st

MIN_FREE_GB = 6.0   # 4bit 7B 推理大约需要 5–6 GB 显存余量


@st.cache_data(show_spinner=False, ttl=300)
def probe() -> dict[str, Any]:
    """返回 ``{"ready": bool, "checks": [...], "blockers": [...]}``。"""
    checks: list[dict[str, Any]] = []
    blockers: list[str] = []

    # ---- 1. 权重目录 ----
    try:
        from src.models.llm.qwen import DEFAULT_BASE_DIR

        base = DEFAULT_BASE_DIR
    except Exception:  # pragma: no cover - 仅在源码被裁剪时触发
        from pathlib import Path

        base = Path("models") / "Qwen2.5-7B-Instruct"
    weights = list(base.glob("*.safetensors")) if base.exists() else []
    size_gb = sum(p.stat().st_size for p in weights) / 1e9
    ok = bool(weights)
    checks.append({
        "name": "基座权重",
        "ok": ok,
        "detail": (f"{base.name} · {len(weights)} 个分片 · {size_gb:.1f} GB"
                   if ok else f"未找到 {base}"),
    })
    if not ok:
        blockers.append("没有 Qwen 权重文件")

    # ---- 2. CUDA / 显存 ----
    try:
        import torch

        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info(0)
            free_gb = free / 1e9
            ok = free_gb >= MIN_FREE_GB
            checks.append({
                "name": "CUDA 显存",
                "ok": ok,
                "detail": (f"{torch.cuda.get_device_name(0)} · 空闲 "
                           f"{free_gb:.1f}/{total / 1e9:.1f} GB"),
            })
            if not ok:
                blockers.append(f"显存不足（需 ≥{MIN_FREE_GB:.0f} GB 空闲，"
                                f"当前 {free_gb:.1f} GB）")
        else:
            checks.append({"name": "CUDA 显存", "ok": False,
                           "detail": "torch.cuda.is_available() = False"})
            blockers.append("CUDA 不可用")
    except Exception as exc:  # noqa: BLE001
        checks.append({"name": "CUDA 显存", "ok": False,
                       "detail": f"torch 导入失败：{exc}"})
        blockers.append("torch 不可用")

    # ---- 3. 依赖 ----
    for mod in ("transformers", "bitsandbytes", "peft", "accelerate"):
        try:
            m = __import__(mod)
            checks.append({"name": mod, "ok": True,
                           "detail": getattr(m, "__version__", "已安装")})
        except Exception as exc:  # noqa: BLE001
            checks.append({"name": mod, "ok": False,
                           "detail": f"{type(exc).__name__}: 未安装"})
            if mod in ("transformers", "bitsandbytes"):
                blockers.append(f"缺 {mod}")

    return {"ready": not blockers, "checks": checks,
            "blockers": blockers, "size_gb": round(size_gb, 1)}


INSTALL_HINT = (
    "装上依赖后，把 Agent 的 llm 参数换成 `get_backend(\"qwen\")` 即可接入："
    "`pip install transformers bitsandbytes accelerate peft`"
)
