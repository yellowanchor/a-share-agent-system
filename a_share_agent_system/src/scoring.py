# -*- coding: utf-8 -*-
"""四因子打分的唯一实现（回测与线上共用）。

为什么要有这个文件：2026-11-24 的回测 IC 检验发现，原来"追动量 + 放量"的打分方向
在 A 股短周期上是**负 alpha**（逐日截面 Spearman IC：trend −0.073、volume −0.035），
只有"低风险"因子是正的（risk +0.123）。因此把因子方向也做成配置项：
``direction`` 取 −1 表示该因子反向使用（100 − v），取 +1 表示正向使用。

打分公式：
    score = Σ  w_k × ( v_k            方向 +1 )
                  ( 100 − v_k        方向 −1 )

四个分项本身仍是 0–100 的分数（50 为中性），所以反向后的分项依旧在 0–100。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

ROOT = Path(__file__).resolve().parents[1]          # a_share_agent_system/
CFG_PATH = ROOT / "configs" / "scoring.yaml"

#: 配置文件缺失或字段缺省时使用的兜底（与 2026-11-24 回测结论一致）
DEFAULT_SPEC: Dict[str, Any] = {
    "weights": {"sentiment": 0.20, "trend": 0.25, "volume": 0.20, "risk": 0.35},
    "direction": {"sentiment": 1, "trend": -1, "volume": -1, "risk": 1},
}

KEYS = ("sentiment", "trend", "volume", "risk")


def load_spec(path: str | Path | None = None) -> Dict[str, Any]:
    """读取打分配置；文件不存在或缺字段时用 DEFAULT_SPEC 补齐。"""
    p = Path(path) if path else CFG_PATH
    spec: Dict[str, Any] = {"weights": dict(DEFAULT_SPEC["weights"]),
                            "direction": dict(DEFAULT_SPEC["direction"])}
    if p.exists():
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        spec["weights"].update(raw.get("weights") or {})
        spec["direction"].update(raw.get("direction") or {})
    return spec


def directed(value: float, key: str, spec: Dict[str, Any] | None = None) -> float:
    """按方向处理单个分项：方向 −1 时返回 100 − value。"""
    s = spec or DEFAULT_SPEC
    return float(value) if s["direction"].get(key, 1) > 0 else 100.0 - float(value)


def combine(parts: Dict[str, float], spec: Dict[str, Any] | None = None) -> float:
    """把四个分项合成为 0–100 的总分。``parts`` 的键同 KEYS。"""
    s = spec or DEFAULT_SPEC
    total = sum(directed(parts[k], k, s) * float(s["weights"][k]) for k in KEYS)
    return round(total, 2)
