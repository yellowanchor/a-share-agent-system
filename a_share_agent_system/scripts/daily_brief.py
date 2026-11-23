"""把 ``data/signals/<date>.json`` 渲染成一份可读的每日简报（Markdown / 纯文本）。

用途：
  * 早晨开盘前在命令行/聊天里快速看今天的推荐与否决理由；
  * 直接粘进论文附录或周报（表格 + 数字都是真实产物）。

用法：
    python scripts/daily_brief.py                 # 最新一份 signals
    python scripts/daily_brief.py --date 2026-11-20
    python scripts/daily_brief.py --md out.md     # 同时写文件
    python scripts/daily_brief.py --top 5         # 详细展开前 5 只
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SIGNAL_DIR = ROOT / "data" / "signals"
VERDICT_CN = {"buy": "建议买入", "watch": "观察", "reject": "否决"}
GATE_CN = {"pass": "通过", "caution": "谨慎", "veto": "否决"}


def latest_signal_file(date: str | None = None) -> Path:
    """定位 signals 文件：指定日期优先，否则取文件名最新的。"""
    if date:
        p = SIGNAL_DIR / f"{date}.json"
        if not p.exists():
            raise FileNotFoundError(f"没有 {date} 的推荐产物：{p}")
        return p
    files = sorted(SIGNAL_DIR.glob("*.json"))
    if not files:
        raise FileNotFoundError(f"{SIGNAL_DIR} 下还没有 signals，请先跑 run_daily_signals.py")
    return files[-1]


def render(payload: dict, top: int = 3) -> str:
    """生成 Markdown 简报。"""
    items = payload.get("items") or []
    as_of = payload.get("as_of") or "?"
    gen = payload.get("generated_at") or payload.get("ts") or "-"

    n_buy = sum(1 for x in items if x.get("verdict") == "buy")
    n_watch = sum(1 for x in items if x.get("verdict") == "watch")
    n_reject = sum(1 for x in items if x.get("verdict") == "reject")

    lines = [
        f"# 每日推荐简报（数据截止 {as_of}）",
        "",
        f"- 生成时间：{gen}",
        f"- 候选 {len(items)} 只：建议买入 **{n_buy}** / 观察 **{n_watch}** / 否决 **{n_reject}**",
        "- 打分 = 舆情 35% + 趋势 25% + 量能 20% + 风险 20%；价格均为**不复权真实价**",
        "",
        "| 排名 | 代码 | 名称 | 综合分 | 结论 | 买入 | 止损 | 止盈 | 仓位 | 闸门 | 证据 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for rank, it in enumerate(items, 1):
        gate = it.get("gate") or {}
        code6 = str(it.get("code", "")).split(".")[-1]
        lines.append(
            f"| {rank} | {code6} | {it.get('name','') or '-'} "
            f"| {it.get('score',0):.1f} | {VERDICT_CN.get(it.get('verdict',''), it.get('verdict',''))} "
            f"| {it.get('entry','-')} | {it.get('stop_loss','-')} | {it.get('take_profit','-')} "
            f"| {it.get('position_pct','-')}% | {GATE_CN.get(gate.get('verdict',''), gate.get('verdict',''))} "
            f"| {len(it.get('evidence') or [])} |"
        )

    rejected = [x for x in items if x.get("verdict") == "reject"]
    if rejected:
        lines += ["", "## 被否决的候选（新闻闸门的作用）", ""]
        for it in rejected:
            gate = it.get("gate") or {}
            reason = gate.get("reason") or "综合分不足"
            hits = "、".join(gate.get("hits") or [])
            lines.append(f"- **{it.get('code')} {it.get('name','') or ''}**：{reason}"
                         + (f"（命中：{hits}）" if hits else ""))

    detail = [x for x in items if x.get("verdict") in ("buy", "watch")][:top]
    if detail:
        lines += ["", f"## 重点标的（前 {len(detail)} 只）", ""]
        for it in detail:
            parts = it.get("parts") or {}
            lines.append(f"### {it.get('code')} {it.get('name','') or ''} — "
                         f"{VERDICT_CN.get(it.get('verdict',''), '')}（{it.get('score',0):.1f} 分）")
            lines.append("")
            lines.append(f"- 分项：舆情 {parts.get('sentiment',0):.0f} / 趋势 {parts.get('trend',0):.0f} "
                         f"/ 量能 {parts.get('volume',0):.0f} / 风险 {parts.get('risk',0):.0f}")
            lines.append(f"- 价位：买 {it.get('entry')} / 止 {it.get('stop_loss')} / 盈 {it.get('take_profit')}"
                         f"，仓位 {it.get('position_pct')}%，周期 {it.get('holding_period') or '-'}")
            if it.get("rationale"):
                lines.append(f"- 逻辑：{str(it['rationale'])[:220]}")
            if it.get("risk_notes"):
                lines.append(f"- 风险：{str(it['risk_notes'])[:200]}")
            ev = it.get("evidence") or []
            if ev:
                lines.append(f"- 证据（{len(ev)} 条）：")
                for e in ev[:3]:
                    tag = {"live": "现场", "recent": "兜底", "semantic": "检索"}.get(e.get("retrieval", ""), "")
                    lines.append(f"  - [{tag}] {str(e.get('publish_time',''))[:10]} {str(e.get('title',''))[:50]}")
            lines.append("")

    lines += ["---", "由多智能体评测 + 新闻闸门生成，非实时行情，**不构成投资建议**；仅供学术研究。"]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="渲染每日推荐简报")
    ap.add_argument("--date", default=None, help="指定日期 YYYY-MM-DD（缺省=最新）")
    ap.add_argument("--top", type=int, default=3, help="详细展开前 N 只（默认 3）")
    ap.add_argument("--md", default=None, help="额外写入 Markdown 文件")
    args = ap.parse_args()

    path = latest_signal_file(args.date)
    payload = json.loads(path.read_text(encoding="utf-8"))
    text = render(payload, top=args.top)
    print(text)
    if args.md:
        Path(args.md).write_text(text, encoding="utf-8")
        print(f"\n已写入 {args.md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
