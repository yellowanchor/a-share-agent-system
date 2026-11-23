# -*- coding: utf-8 -*-
"""多智能体协作模块

Phase 3 已实现的三 Agent 工作流（Phase 3.2 起由编排层驱动，带迭代闭环）:

    Analyst（舆情研判）→ ┌→ Strategist → Critic ─┐
                        └──── 不通过则带反馈重试 ←──┘

数据流契约见 `src/agents/schemas.py`：
    AnalystReport → StrategyAdvice → CriticVerdict

快速开始:
    from src.agents import Orchestrator, AnalystAgent, StrategistAgent, CriticAgent
    from src.models.llm import get_backend

    # 推荐：走编排层（含 Critic 不通过自动重试）
    orch = Orchestrator(llm=get_backend("qwen"))
    result = orch.run({"stock_code": "600519", "as_of": "2026-08-20"})["result"]

    # 也可手动串联（调试单个 Agent 时用）
    llm = get_backend("mock")            # 离线联调改用 mock
    report  = AnalystAgent(llm=llm).run({"stock_code": "600519"})["report"]
    advice  = StrategistAgent(llm=llm).run({"report": report})["advice"]
    verdict = CriticAgent(llm=llm).run({"report": report, "advice": advice})["verdict"]
"""

from src.agents.analyst import AnalystAgent
from src.agents.base import AgentConfig, AgentMessage, BaseAgent
from src.agents.critic import CriticAgent
from src.agents.orchestrator import (
    Orchestrator,
    OrchestratorConfig,
    PipelineResult,
    RoundRecord,
)
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
    # 编排层（Phase 3.2）
    "Orchestrator", "OrchestratorConfig", "PipelineResult", "RoundRecord",
    # 数据契约
    "Evidence", "AnalystReport", "StrategyAdvice",
    "CriticIssue", "CriticVerdict",
]
