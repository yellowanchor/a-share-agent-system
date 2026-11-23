# -*- coding: utf-8 -*-
"""base.py — LLM 后端抽象与 Mock 实现

为什么要抽象
------------
Agent 逻辑（提示词编排、证据引用、结果解析）应当与「模型怎么加载」解耦。
这样带来三个直接收益：
    - 换底座（Qwen2.5-7B → 其他）只需替换后端，不动 Agent 代码
    - 无 GPU / CI 环境可用 MockLLMBackend 跑通整条流水线
    - 论文可把「模型可替换」写成设计特性而非口头承诺

用法:
    from src.models.llm import get_backend
    llm = get_backend("mock")            # 离线联调用
    llm = get_backend("qwen")            # 真实推理
    text = llm.chat(system="...", user="...")
    data = llm.chat_json(system="...", user="...", default={...})
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from src.utils.json_utils import parse_json_loose

logger = logging.getLogger(__name__)

__all__ = ["LLMBackend", "MockLLMBackend", "get_backend"]


class LLMBackend(ABC):
    """LLM 后端抽象基类。

    子类需实现 `chat`；`chat_json` 基于 `chat` 复用容错解析。
    """

    #: 后端名称，用于日志与报告标注
    name: str = "base"

    # ------------------------------------------------------------------
    # 抽象接口
    # ------------------------------------------------------------------
    @abstractmethod
    def chat(
        self,
        system: str,
        user: str,
        temperature: float = 0.1,
        max_new_tokens: int = 1024,
        role: str = "",
        **kwargs: Any,
    ) -> str:
        """执行一轮对话，返回模型输出文本。

        Args:
            system: 系统提示词。
            user: 用户提示词。
            temperature: 采样温度。
            max_new_tokens: 最大生成长度。
            role: 调用方角色标识（如 analyst/strategist/critic）。
                  真实后端忽略该参数；Mock 后端据此选择预设输出。
            **kwargs: 后端特有参数。
        """
        ...

    # ------------------------------------------------------------------
    # 通用能力
    # ------------------------------------------------------------------
    def chat_json(
        self,
        system: str,
        user: str,
        temperature: float = 0.1,
        max_new_tokens: int = 1024,
        default: Optional[Dict[str, Any]] = None,
        role: str = "",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """对话并解析为 JSON dict。

        Args:
            system / user / temperature / max_new_tokens: 同 `chat`。
            default: 解析失败时的兜底结果（None 时返回空 dict）。
            role: 调用方角色标识，透传给 `chat`。

        Returns:
            解析结果或兜底值。失败会记录 WARNING 日志，便于离线排查。
        """
        raw = self.chat(system, user, temperature=temperature,
                        max_new_tokens=max_new_tokens, role=role, **kwargs)
        parsed = parse_json_loose(raw)
        if parsed is None:
            logger.warning("[%s] JSON 解析失败，输出前 200 字: %s",
                           self.name, raw[:200])
            return dict(default or {})
        return parsed

    def close(self) -> None:
        """释放资源（默认无操作，GPU 后端覆写）。"""

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{type(self).__name__} name={self.name}>"


class MockLLMBackend(LLMBackend):
    """离线 Mock 后端：按提示词关键词返回预设 JSON。

    用途:
        - 单元测试 / CI：无需 16GB 显存即可验证 Agent 编排逻辑
        - 故障演练：强制返回非法 JSON，验证降级分支是否可靠

    注意:
        Mock 结果不具备金融意义，**严禁**用于论文实验结果。
    """

    name = "mock"

    def __init__(self, mode: str = "valid", note: str = "") -> None:
        """初始化。

        Args:
            mode: "valid" 返回合法 JSON；"invalid" 返回无法解析的文本。
            note: 附加到输出中的标记串，便于追踪。
        """
        self.mode = mode
        self.note = note
        self.calls: list = []       # 记录全部调用（测试断言用）

    def chat(
        self,
        system: str,
        user: str,
        temperature: float = 0.1,
        max_new_tokens: int = 1024,
        role: str = "",
        **kwargs: Any,
    ) -> str:
        """返回与角色匹配的预设 JSON。

        路由优先级：`role` 参数 > 提示词关键词。
        之所以以 role 为准，是因为提示词文案里可能交叉出现其他角色的
        关键词（例如 Analyst 提示词会提到「量化策略环节」），关键词匹配
        并不可靠。
        """
        self.calls.append({"system": system, "user": user, "role": role})
        if self.mode == "invalid":
            return "抱歉，我无法完成该分析任务。" + self.note

        r = (role or "").strip().lower()
        if r in ("analyst", "舆情", "分析师"):
            return _mock_analyst(self.note)
        if r in ("strategist", "策略"):
            return _mock_strategy(self.note)
        if r in ("critic", "校验", "风控"):
            return _mock_critic(self.note)

        # 兜底：关键词匹配（仅在未传 role 时生效）
        sys_l = system or ""
        if "量化策略研究员" in sys_l:
            return _mock_strategy(self.note)
        if "风控审计专家" in sys_l:
            return _mock_critic(self.note)
        return _mock_analyst(self.note)


# ---------------------------------------------------------------------------
# Mock 预设输出
# ---------------------------------------------------------------------------
def _mock_analyst(note: str) -> str:
    """Analyst 的 Mock 输出（含证据引用）。"""
    return (
        '{"summary": "近期新闻以中性偏正面为主，成交量温和放大，短线情绪回暖。'
        + note
        + '", "sentiment": "neutral", "confidence": 0.55, "impact": "moderate",'
        ' "themes": ["业绩", "资金流向"], "valid_until": "未来10个交易日",'
        ' "evidence_ids": [1, 2]}'
    )


def _mock_strategy(note: str) -> str:
    """Strategist 的 Mock 输出。"""
    return (
        '{"direction": "watch", "position_pct": 5.0, "entry": null,'
        ' "stop_loss": null, "take_profit": null,'
        ' "holding_period": "5-10个交易日",'
        ' "invalidation": "跌破近60日支撑位或负面新闻集中出现",'
        ' "rationale": "舆情中性，等待更明确信号[证据 1]",'
        ' "evidence_ids": [1], "risk_notes": "注意大盘系统性风险",'
        ' "confidence": 0.5}' + ("" if not note else "")
    )


def _mock_critic(note: str) -> str:
    """Critic 的 Mock 输出。"""
    return (
        '{"issues": [{"code": "X999", "severity": "info",'
        ' "message": "Mock 校验通过", "fix": ""}],'
        ' "summary": "Mock 模式：未发现结构性问题' + note + '"}'
    )


def get_backend(kind: str = "qwen", **kwargs: Any) -> LLMBackend:
    """工厂函数：按名称创建后端。

    Args:
        kind: "qwen" 真实推理 / "mock" 离线桩。
        **kwargs: 透传给具体后端构造函数。

    Returns:
        LLMBackend 实例。
    """
    if kind == "mock":
        return MockLLMBackend(**kwargs)
    if kind in ("qwen", "qwen2.5", "default"):
        from src.models.llm.qwen import QwenBackend
        return QwenBackend(**kwargs)
    raise ValueError(f"未知后端: {kind}，可选 qwen / mock")
