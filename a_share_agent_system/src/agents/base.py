"""BaseAgent — 智能体抽象基类

定义所有多智能体的统一接口，预留商用扩展接口。
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class AgentConfig(BaseModel):
    """Agent 配置模型。

    Attributes:
        name: Agent 名称，用于日志和消息路由。
        temperature: LLM 温度参数（0-2）。
        max_tokens: 最大生成 token 数。
        system_prompt: 系统提示词模板路径。
    """
    name: str = "base"
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=1, le=32768)
    system_prompt: Optional[str] = None


class AgentMessage(BaseModel):
    """Agent 间通信的标准消息格式。

    Attributes:
        sender: 发送方 Agent 名称。
        recipient: 接收方 Agent 名称（None 为广播）。
        content: 消息内容（字符串或结构化数据）。
        metadata: 附加元数据（如时间戳、置信度等）。
    """
    sender: str
    recipient: Optional[str] = None
    content: Any
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BaseAgent(ABC):
    """多智能体抽象基类。

    所有具体 Agent（Analyst / Strategist / Critic）必须继承此类。

    预留商用接口:
        - trade_hooks: 交易执行钩子（预留，Phase 3+）
        - risk_module: 风控模块位置（预留）
        - message_bus: 消息总线接口（预留扩展为 MQ/Kafka）

    Attributes:
        config: Agent 配置。
        llm: 语言模型实例（vLLM/Ollama/LangChain 封装）。
    """

    def __init__(
        self,
        config: AgentConfig,
        llm: Optional[Any] = None,
    ) -> None:
        """初始化 Agent。

        Args:
            config: Agent 配置对象。
            llm: LLM 实例，若为 None 则使用默认本地模型。
        """
        self.config = config
        self.llm = llm
        self._history: List[AgentMessage] = []

        # === 商用预留接口（Phase 1 暂未启用）===
        self.trade_hooks: List[Any] = []     # 交易执行钩子
        self._risk_module: Optional[Any] = None  # 风控模块占位

    # ------------------------------------------------------------------
    #  抽象方法
    # ------------------------------------------------------------------

    @abstractmethod
    async def process(self, message: AgentMessage) -> AgentMessage:
        """处理输入消息并返回响应。

        核心推理逻辑，由子类实现具体的分析/策略/校验流程。

        Args:
            message: 输入消息。

        Returns:
            处理后的响应消息。
        """
        ...

    @abstractmethod
    async def run(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """执行 Agent 主任务。

        封装完整的单次推理流水线: 预处理 -> 推理 -> 后处理 -> 输出。

        Args:
            input_data: 输入数据字典（如股票代码、时间范围、舆情数据等）。

        Returns:
            处理结果字典。
        """
        ...

    # ------------------------------------------------------------------
    #  通用方法
    # ------------------------------------------------------------------

    async def send_message(
        self,
        recipient: str,
        content: Any,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentMessage:
        """向其他 Agent 发送消息。

        Args:
            recipient: 接收方 Agent 名称。
            content: 消息内容。
            metadata: 附加元数据。

        Returns:
            构造的消息对象。
        """
        msg = AgentMessage(
            sender=self.config.name,
            recipient=recipient,
            content=content,
            metadata=metadata or {},
        )
        logger.debug(
            "Agent [%s] -> [%s]: %s",
            self.config.name, recipient,
            str(content)[:100],
        )
        return msg

    def add_to_history(self, message: AgentMessage) -> None:
        """将消息添加到对话历史。

        Args:
            message: 要记录的消息。
        """
        self._history.append(message)

    def get_history(self) -> List[AgentMessage]:
        """获取完整对话历史。

        Returns:
            消息历史列表。
        """
        return self._history.copy()

    def clear_history(self) -> None:
        """清空对话历史。"""
        self._history.clear()

    # ------------------------------------------------------------------
    #  商用预留接口（Phase 1 仅声明，Phase 3+ 实现）
    # ------------------------------------------------------------------

    def register_trade_hook(self, hook: Any) -> None:
        """注册交易执行钩子（预留）。

        Args:
            hook: 交易钩子对象（需实现 execute 方法）。
        """
        self.trade_hooks.append(hook)
        logger.info("Agent [%s] 注册交易钩子: %s", self.config.name, type(hook).__name__)

    def attach_risk_module(self, risk_module: Any) -> None:
        """挂载风控模块（预留）。

        Args:
            risk_module: 风控模块实例。
        """
        self._risk_module = risk_module
        logger.info("Agent [%s] 挂载风控模块", self.config.name)
