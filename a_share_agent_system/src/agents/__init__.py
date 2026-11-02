# -*- coding: utf-8 -*-
"""多智能体协作模块

Phase 3 已实现的三 Agent 工作流:

    Analyst（舆情研判）→ Strategist（策略生成）→ Critic（反思校验）

数据流契约见 `src/agents/schemas.py`：
    AnalystReport → StrategyAdvice → CriticVerdict

快速开始:
    from src.agents import AnalystAgent, StrategistAgent, CriticAgent
    from src.models.llm import get_backend

    llm = get_backend("qwen")            # 离线联调改用 get_backend("mock")
    report  = AnalystAgent(llm=llm).run({"stock_code": "600519"})["report"]
    advice  = StrategistAgent(llm=llm).run({"report": report})["advice"]
    verdict = CriticAgent(llm=llm).run({"report": report, "advice": advice})["verdict"]
"""

from src.agents.analyst import AnalystAgent
from src.agents.base import AgentConfig, AgentMessage, BaseAgent
from src.agents.critic import CriticAgent
from src.agents.schemas import (
    AnalystReport,
    CriticIssue,
    CriticVerdict,
    Evidence,
    StrategyAdvice,
)
from src.agents.strategist import StrategistAgent

__all__ = [
    # 基类与通信
    "BaseAgent", "AgentConfig", "AgentMessage",
    # 三个 Agent
    "AnalystAgent", "StrategistAgent", "CriticAgent",
    # 数据契约
    "Evidence", "AnalystReport", "StrategyAdvice",
    "CriticIssue", "CriticVerdict",
]
