# -*- coding: utf-8 -*-
"""json_utils.py — 大语言模型输出 JSON 的容错解析

背景
----
7B 级别模型在严格 JSON 输出上并不稳定，常见退化形式：
    - 用 ```json ... ``` 代码块包裹
    - 前后带解释性文字（"好的，以下是分析结果：\n{...}"）
    - 尾部截断（max_new_tokens 用尽）
    - 中文引号 / 尾逗号
逐条 try/except 会让 Agent 代码充满噪声，这里统一收敛为一个入口。

策略（由严到宽）：
    1. 直接 json.loads
    2. 剥离 ``` 围栏后 json.loads
    3. 截取首个 '{' 到最后一个 '}' 后 json.loads
    4. 修复中文引号/尾逗号后重试
    5. 全部失败返回 None（调用方决定降级路径）
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

__all__ = ["parse_json_loose", "extract_json_block"]

#: ```json ... ``` 或 ``` ... ``` 围栏
_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)
#: 尾逗号（, 后紧跟 } 或 ]）
_TRAILING_COMMA = re.compile(r",\s*([}\]])")
#: 中文引号 → 英文引号
_CN_QUOTE = {ord("“"): ord('"'), ord("”"): ord('"'),
             ord("‘"): ord("'"), ord("’"): ord("'")}


def extract_json_block(text: str) -> Optional[str]:
    """从文本中截取最外层 {...} 片段（贪心匹配首尾大括号）。"""
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start:end + 1]


def parse_json_loose(text: str) -> Optional[dict]:
    """容错解析 LLM 输出为 dict。

    Args:
        text: 模型原始输出。

    Returns:
        解析成功的 dict；彻底失败时返回 None（不抛异常，交由上层降级）。
    """
    if not text or not text.strip():
        return None

    candidates = [text.strip()]

    # 2. 剥离代码围栏
    for m in _FENCE.finditer(text):
        candidates.append(m.group(1).strip())

    # 3. 截取 {...}
    block = extract_json_block(text)
    if block:
        candidates.append(block)

    for cand in candidates:
        # 1/2/3 直接解析
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
        # 4. 轻度修复后重试
        fixed = _TRAILING_COMMA.sub(r"\1", cand.translate(_CN_QUOTE))
        try:
            obj = json.loads(fixed)
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue

    # 5. 尾部截断补救：尝试补齐缺失的右括号
    if block:
        repaired = block
        for _ in range(8):
            try:
                obj = json.loads(repaired)
                if isinstance(obj, dict):
                    return obj
            except Exception:
                repaired += "}"
        # 截断处可能停在字符串中间，去掉最后一个不完整键值再补
        cut = repaired.rfind('",')
        if cut != -1:
            try:
                obj = json.loads(repaired[:cut] + '"}')
                if isinstance(obj, dict):
                    return obj
            except Exception:
                pass
    return None


def dumps_cn(obj: Any, **kwargs) -> str:
    """中文友好的 JSON 序列化（ensure_ascii=False）。"""
    kwargs.setdefault("ensure_ascii", False)
    kwargs.setdefault("indent", 2)
    return json.dumps(obj, **kwargs)
