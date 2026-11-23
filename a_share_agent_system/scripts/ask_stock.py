"""实时问股：分析一只股票并给出"能不能买"的结论（含现场新闻 + 新闻闸门）。

与 UI 的静态看板不同，本脚本面向"我现在想查某只股票"的场景：
  1. L2 现场抓该股最新新闻（``live_news=True``，6 小时缓存；历史 as_of 自动跳过）；
  2. 三 Agent 流水线（分析师 → 策略师 → 评审）+ 新闻闸门 veto/caution/pass；
  3. 输出不复权的真实价格区间（成本价、止损、止盈、仓位）。

用法：
    python scripts/ask_stock.py --code 600519
    python scripts/ask_stock.py --code sz.000001 --no-live        # 只用本地 RAG
    python scripts/ask_stock.py --code 600519 --as-of 2026-08-20  # 历史复现（自动关掉 L2）
    python scripts/ask_stock.py --code 600519 --json               # 机器可读

退出码：0 = 成功（无论结论）；1 = 参数/运行错误。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logger = logging.getLogger("ask_stock")

VERDICT_CN = {"buy": "建议买入", "add": "建议加仓", "hold": "建议持有",
              "reduce": "建议减仓", "exit": "建议清仓", "avoid": "建议回避"}
GATE_CN = {"pass": "通过", "caution": "谨慎", "veto": "否决"}


def _six(code: str) -> str:
    """``sh.600519`` / ``600519`` → ``600519``。"""
    return str(code).strip().lower().split(".")[-1].strip()


def _full_code(code: str) -> str:
    """补全交易所前缀（6 开头→sh，其余→sz）。"""
    code = str(code).strip().lower()
    if "." in code:
        return code
    return ("sh." if code.startswith(("6", "9")) else "sz.") + code


def _raw_price(store, code: str, as_of: str | None, price: float | None) -> tuple[float | None, float | None]:
    """把前复权价换算成不复权真实价，返回 (真实价, 当日因子)。"""
    if price is None:
        return None, None
    try:
        df = store.daily_range(code, end=as_of, adjust="raw")
        if df is None or df.empty:
            return None, None
        factor = float(df["hfq_factor"].iloc[-1])
        if factor <= 0:
            return None, None
        return round(price / factor, 2), factor
    except Exception as exc:  # noqa: BLE001
        logger.warning("复权因子查询失败 %s: %s", code, exc)
        return None, None


def build(code: str, as_of: str | None, live: bool) -> dict:
    """跑一遍流水线 + 闸门，返回结构化结果。"""
    from src.agents.news_gate import NewsGate
    from src.agents.orchestrator import Orchestrator

    oc = Orchestrator(live_news=live)
    payload = oc.run({"stock_code": _full_code(code), "as_of": as_of})
    result = payload["result"]           # Orchestrator.run 返回 {result, report_out, elapsed_ms}
    report = result.report
    advice = result.advice

    gate = NewsGate().check(report, getattr(report, "market_snapshot", None))

    store = oc.analyst.store
    entry, factor = _raw_price(store, _full_code(code), as_of, getattr(advice, "entry", None))
    stop = _raw_price(store, _full_code(code), as_of, getattr(advice, "stop_loss", None))[0]
    take = _raw_price(store, _full_code(code), as_of, getattr(advice, "take_profit", None))[0]
    snap = report.market_snapshot
    snap_close = snap.get("close") if isinstance(snap, dict) else getattr(snap, "close", None)
    close = _raw_price(store, _full_code(code), as_of, snap_close)[0]

    evidence = []
    for i, ev in enumerate(report.evidence or []):
        evidence.append({
            "idx": i + 1,
            "retrieval": getattr(ev, "retrieval", "semantic"),
            "title": ev.title,
            "source": ev.source,
            "publish_time": str(ev.publish_time)[:19],
            "sentiment": ev.sentiment,
            "confidence": ev.confidence,
            "url": getattr(ev, "url", "") or "",
            "snippet": ev.snippet,
        })

    return {
        "code": _six(code),
        "full_code": _full_code(code),
        "as_of": as_of or "最新",
        "live_news": bool(live and not as_of),
        "direction": advice.direction,
        "direction_cn": VERDICT_CN.get(str(advice.direction), str(advice.direction)),
        "position_pct": advice.position_pct,
        "entry": entry,
        "stop_loss": stop,
        "take_profit": take,
        "close": close,
        "hfq_factor": factor,
        "holding_period": getattr(advice, "holding_period", ""),
        "thesis": advice.rationale,
        "risk_notes": [advice.risk_notes] if advice.risk_notes else [],
        "invalidation": getattr(advice, "invalidation", ""),
        "gate": gate.to_dict(),
        "critic": {
            "passed": bool(result.verdict.passed),
            "n_error": result.verdict.n_error,
            "n_warning": result.verdict.n_warning,
            "summary": result.verdict.summary,
        },
        "evidence": evidence,
    }


def _print_human(r: dict) -> None:
    gate = r["gate"]
    print("=" * 66)
    print(f"{r['code']}  {r['as_of']}  {'（现场新闻已启用）' if r['live_news'] else '（仅本地 RAG）'}")
    print("=" * 66)
    print(f"结论      : {r['direction_cn']}（direction={r['direction']}）")
    print(f"仓位建议  : {r['position_pct']}%    持有周期: {r['holding_period'] or '-'}")
    print(f"成本/买入 : {r['entry']}    止损: {r['stop_loss']}    止盈: {r['take_profit']}    现价: {r['close']}")
    print(f"新闻闸门  : {GATE_CN.get(gate['verdict'], gate['verdict'])}（{gate['reason'] or '无'}）")
    if gate.get("hits"):
        print(f"触发关键词: {'、'.join(gate['hits'])}")
    print(f"评审      : {'通过' if r['critic']['passed'] else '未通过'}"
          f"（error {r['critic']['n_error']} / warning {r['critic']['n_warning']}）{r['critic']['summary']}")
    print("-" * 66)
    print("投资逻辑  :", r["thesis"])
    if r["risk_notes"]:
        print("风险提示  :")
        for note in r["risk_notes"]:
            print("  -", note)
    if r["evidence"]:
        print("-" * 66)
        print("证据链（retrieval: live=现场抓取 / semantic=向量检索 / recent=结构化兜底）:")
        for ev in r["evidence"]:
            print(f"  [{ev['idx']}] {ev['retrieval']:8s} {ev['publish_time']} "
                  f"{ev['sentiment']:6s} {ev['title'][:44]}")
            if ev["snippet"]:
                print(f"      {ev['snippet'][:100]}")
    else:
        print("证据链    : 无（未检索到相关新闻，闸门将按证据不足给 caution）")
    print("=" * 66)


def main() -> int:
    ap = argparse.ArgumentParser(description="实时问股：新闻 + Agent 判断能否买入")
    ap.add_argument("--code", required=True, help="股票代码，如 600519 / sz.000001")
    ap.add_argument("--as-of", default=None, help="分析基准日 YYYY-MM-DD（默认最新）")
    ap.add_argument("--no-live", action="store_true", help="关闭现场新闻抓取，只用本地 RAG")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    try:
        result = build(args.code, args.as_of, live=not args.no_live)
    except Exception as exc:  # noqa: BLE001
        logging.exception("问股失败: %s", exc)
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        _print_human(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
