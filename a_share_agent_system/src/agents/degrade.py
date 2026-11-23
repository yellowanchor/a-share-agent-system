# -*- coding: utf-8 -*-
"""degrade.py — 把「这次为什么走规则路径」翻译成人话。

三 Agent 都支持「LLM 不可用 → 规则路径」降级，但降级点分散在
Analyst / Strategist / Critic / Orchestrator 四处，看板只显示
``llm_used=False`` 时用户不知道到底卡在哪。这里统一解释：

- 是**没接模型**（调用方传了 ``llm=None``）；
- 还是**接了但调用失败**（显存不足 / 权重缺失 / 超时 / 输出不是合法 JSON）；
- 还是**校验层只跑了确定性规则**（Critic 的第二层语义校验被关掉）；
- 以及**编排层的最终降级**（两轮都没过 → 观望 0 仓位）。

纯函数、无副作用，便于前端与报告复用。
"""

from __future__ import annotations

from typing import Any

NO_LLM_HINT = (
    "本次没有把大模型接进流水线（`Orchestrator(llm=None)`）：结论由词典情感统计、"
    "模板与规则阈值生成，速度快（1–2 秒），但不做语义推理。"
)
LLM_FAIL_HINT = (
    "模型已接入但调用失败或输出无法解析为 JSON：常见原因是显存不足、"
    "权重/依赖缺失（`transformers`、`bitsandbytes`）或超时；"
    "详细报错见 `logs/` 与 Agent 的 WARNING 日志。"
)


def explain(
    result: dict[str, Any],
    *,
    llm_attached: bool = False,
    tier: str = "max",
) -> list[dict[str, str]]:
    """生成降级原因条目。

    Args:
        result: ``PipelineResult.to_json_dict()`` 的结果。
        llm_attached: 本次是否把 LLM 后端传给了 Orchestrator。
    tier: ``"max"`` 三层都接大模型；``"eco"`` 经济档（只有 Analyst 接）。

    Returns:
        ``[{"stage", "level", "reason", "hint"}, ...]``；level 取
        ``规则`` / ``降级`` / ``提示`` 三档，前端按此着色。
    """
    rows: list[dict[str, str]] = []
    rep = result.get("report") or {}
    adv = result.get("advice") or {}
    ver = result.get("verdict") or {}
    rounds = result.get("rounds") or []
    evidence = rep.get("evidence") or []

    def add(stage: str, level: str, reason: str, hint: str = "") -> None:
        rows.append({"stage": stage, "level": level,
                     "reason": reason, "hint": hint})

    # ---- 三个 Agent 各自的 LLM 使用情况 ----
    for stage, payload in (("Analyst 舆情研判", rep),
                           ("Strategist 策略生成", adv),
                           ("Critic 质量校验", ver)):
        if payload.get("llm_used"):
            continue
        if tier == "eco" and llm_attached and not stage.startswith("Analyst"):
            add(stage, "提示", "经济档：这一层走规则路径",
                "eco 只让 Analyst 用大模型以省时间；要三层都上大模型，把顶栏档位切到 max。")
        elif llm_attached:
            add(stage, "降级", "已接入模型但本次未用上", LLM_FAIL_HINT)
        elif stage.startswith("Critic"):
            add(stage, "规则",
                "只跑了确定性规则层（证据编号 / 仓位上限 / 止损方向 / 数值偏离）",
                "第二层 LLM 语义复核需要 `llm_critic=True` 且模型可用。")
        else:
            add(stage, "规则", "未使用大模型，走规则路径", NO_LLM_HINT)

    # ---- 证据链 ----
    n_ev = len(evidence)
    if n_ev == 0:
        add("证据链（RAG）", "降级", "没有检索到任何证据",
            "结论仅依赖行情与财务快照，置信度会明显偏低。")
    elif all((ev.get("retrieval") or "") == "recent" for ev in evidence):
        add("证据链（RAG）", "提示", "语义检索无匹配，全部回退为「最近新闻」",
            "多为新闻库里缺少该股近期相关文本；建议确认新闻是否已入库到当日。")
    elif all((ev.get("retrieval") or "") != "live" for ev in evidence):
        add("证据链（RAG）", "提示", f"{n_ev} 条证据全部来自本地库，未抓当天新闻",
            "勾选「现场抓当天新闻」可补上当日消息面。")

    # ---- 编排层终局 ----
    final = str(result.get("final_source") or "")
    if final == "fallback":
        add("编排层（终局）", "降级",
            "两轮校验均未通过且无法自动修正 → 已降级为「观望 + 0 仓位」",
            "这是保守兜底，不是模型故障；看 Critic 的具体问题项即可知道卡在哪条规则。")
    elif final == "revised":
        add("编排层（终局）", "提示", "采纳了 Critic 给出的自动修正版",
            "首轮结论未通过校验，修正后通过；属正常闭环，不是降级。")
    elif len(rounds) > 1:
        add("编排层（终局）", "提示",
            f"触发了重试（共 {len(rounds)} 轮）",
            "首轮被 Critic 判为不通过，反馈注入 Strategist 后重试成功。")

    if not rows:
        add("全链路", "提示", "本次全程使用大模型，无降级", "")
    return rows
