# -*- coding: utf-8 -*-
"""LLM 推理后端

对外只暴露 `get_backend`，Agent 层不感知具体模型实现。

    from src.models.llm import get_backend
    llm = get_backend("mock")     # 离线联调（无显存占用）
    llm = get_backend("qwen")     # Qwen2.5-7B-Instruct 4bit 真实推理
"""

from src.models.llm.base import (
    LLMBackend,
    MockLLMBackend,
    get_backend,
)

__all__ = ["LLMBackend", "MockLLMBackend", "get_backend"]
