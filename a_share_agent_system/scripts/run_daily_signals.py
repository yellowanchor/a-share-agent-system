# -*- coding: utf-8 -*-
"""run_daily_signals.py — 每日推荐股（新闻闸门 → 综合打分 → signals JSON）
=========================================================================

流水线::

    候选池（流动性 + 异动）
        ↓  三 Agent（Analyst → Strategist → Critic，规则模式，不依赖 GPU）
    新闻闸门（规则硬闸 + 情感闸 + 证据充足性）
        ↓  综合打分（舆情 35% + 趋势 25% + 量能 20% + 风险 20%）
    data/signals/YYYY-MM-DD.json  +  data/signals/history.csv

为什么闸门要放在打分之前：一旦命中"立案调查/业绩预亏/清仓减持"，无论趋势与量能
多好都不能给买入——这是本项目"舆情先于量化"的策略约束，也是论文对比实验的核心
变量（有闸门 vs 无闸门，看推荐组合后续 5 日收益差）。

用法::

    # 用库内最新交易日，跑前 20 只候选
    python scripts/run_daily_signals.py

    # 指定日期与数量
    python scripts/run_daily_signals.py --as-of 2026-11-21 --limit 30

    # 只看结果不落盘
    python scripts/run_daily_signals.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from src.agents.news_gate import NewsGate  # noqa: E402
from src.agents.orchestrator import Orchestrator  # noqa: E402
from src.data.collect.pool import build_daily_pool  # noqa: E402
from src.data.storage.market_store import MarketStore  # noqa: E402
from src.utils.indicators import summarize_market  # noqa: E402

SIGNAL_DIR = ROOT / "data" / "signals"
CONFIG_PATH = ROOT / "configs" / "scoring.yaml"
NAME_PATH = ROOT / "data" / "reference" / "stock_names.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("signals")


# ----------------------------------------------------------------------
#  配置
# ----------------------------------------------------------------------
def load_config(path: Path = CONFIG_PATH) -> Dict[str, Any]:
    """读打分配置；缺文件时用内置默认值（保证脚本永远能跑）。"""
    default = {
        "weights": {"sentiment": 0.35, "trend": 0.25, "volume": 0.20, "risk": 0.20},
        "gate": {"min_sentiment": -0.35, "caution_sentiment": -0.15, "min_evidence": 2},
        "verdict": {"buy_min": 65, "watch_min": 45, "max_position_pct": 20},
        "risk": {"max_atr_pct": 6.0, "max_annual_vol": 0.60, "veto_score_cap": 40},
    }
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for k, v in default.items():
            if isinstance(v, dict):
                merged = dict(v)
                merged.update(data.get(k) or {})
                default[k] = merged
            elif k not in data:
                data[k] = v
    except Exception as e:  # noqa: BLE001
        logger.warning("读 %s 失败，用默认配置: %s", path, str(e)[:80])
    return default


def load_names() -> Dict[str, str]:
    """股票名映射（可选；缺失时前端显示代码）。"""
    if NAME_PATH.exists():
        try:
            return json.loads(NAME_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


# ----------------------------------------------------------------------
#  打分
# ----------------------------------------------------------------------
def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(x)))


def score_components(snap: Dict[str, Any], gate_score: float, gate_verdict: str,
                     cfg: Dict[str, Any]) -> Dict[str, float]:
    """四个分项打分（各 0–100）。NaN 一律当"未知"，给中性 50。"""
    # 1) 舆情：-1..1 → 0..100
    s_sent = _clip(50.0 + 50.0 * float(gate_score or 0.0))

    # 2) 趋势：均线排列 + 20 日动量
    close = _num(snap.get("close"))
    ma5, ma20 = _num(snap.get("ma5")), _num(snap.get("ma20"))
    ret20 = _num(snap.get("ret_20d"))
    s_trend = 50.0
    if ret20 is not None:
        s_trend += max(-25.0, min(25.0, ret20 * 1.5))       # 20 日 ±15% → ±22.5
    if close and ma20:
        s_trend += 8.0 if close > ma20 else -8.0
    if ma5 and ma20:
        s_trend += 7.0 if ma5 > ma20 else -7.0
    s_trend = _clip(s_trend)

    # 3) 量能：5 日量比，1.0 为中性，1.5–2.5 视为放量健康
    vr = _num(snap.get("vol_ratio"))
    if vr is None:
        s_vol = 50.0
    elif vr <= 1.0:
        s_vol = 40.0 + (vr - 0.5) * 20.0
    else:
        s_vol = 60.0 + min(30.0, (vr - 1.0) * 40.0) - max(0.0, (vr - 3.0) * 15.0)
    s_vol = _clip(s_vol)

    # 4) 风险：ATR 占比 + 年化波动 + 闸门降级
    r_cfg = cfg["risk"]
    s_risk = 100.0
    atr_pct = _num(snap.get("atr_pct"))
    if atr_pct is not None:
        s_risk -= max(0.0, (atr_pct - r_cfg["max_atr_pct"]) * 6.0)
    a_vol = _num(snap.get("annual_vol"))
    if a_vol is not None:
        s_risk -= max(0.0, (a_vol - r_cfg["max_annual_vol"]) * 60.0)
    if gate_verdict == "caution":
        s_risk -= 15.0
    elif gate_verdict == "veto":
        s_risk -= 60.0
    s_risk = _clip(s_risk)

    return {"sentiment": round(s_sent, 1), "trend": round(s_trend, 1),
            "volume": round(s_vol, 1), "risk": round(s_risk, 1)}


def total_score(parts: Dict[str, float], cfg: Dict[str, Any]) -> float:
    w = cfg["weights"]
    total = sum(parts[k] * float(w[k]) for k in ("sentiment", "trend", "volume", "risk"))
    return round(_clip(total), 1)


def _factor_at(store: MarketStore, code: str, as_of: str) -> float:
    """取 as_of 当日的复权因子（raw = hfq / factor）；查不到返回 1.0。"""
    try:
        df = store.daily_range(code, start=None, end=as_of, adjust="raw")
        if df is None or not len(df):
            return 1.0
        f = _num(df.iloc[-1].get("hfq_factor"))
        return f if f and f > 0 else 1.0
    except Exception:  # noqa: BLE001
        return 1.0


def _to_raw(price: Optional[float], factor: float) -> Optional[float]:
    """把前复权价换算成不复权价（用户看到的价格必须是真实成交价）。"""
    if price is None or factor <= 0:
        return None
    try:
        return round(float(price) / factor, 2)
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------
#  单只股票
# ----------------------------------------------------------------------
def analyze_one(orch: Orchestrator, gate: NewsGate, store: MarketStore,
                code: str, as_of: str, cfg: Dict[str, Any],
                lookback_days: int = 180) -> Dict[str, Any]:
    """跑三 Agent + 闸门 + 打分，返回一条推荐记录。"""
    out = orch.run({"stock_code": code, "as_of": as_of})
    result = out["result"]
    # PipelineResult 字段：report(AnalystReport) / advice(StrategyAdvice) / verdict / final_pass
    report = result.report
    advice = result.advice

    decision = gate.check(report)

    # 行情快照：优先用 Analyst 已算好的（它内部按 as_of 截断，PIT 安全）
    snap = dict(report.market_snapshot or {})
    if not snap.get("close"):
        try:
            df = store.daily_range(code, start=None, end=as_of, adjust="hfq")
            df = df.tail(lookback_days) if df is not None and len(df) else df
            snap = summarize_market(df, code).to_dict()
        except Exception as e:  # noqa: BLE001
            logger.warning("行情快照失败 %s: %s", code, str(e)[:80])
            snap = {}

    parts = score_components(snap, decision.sentiment_score, decision.verdict, cfg)
    score = total_score(parts, cfg)

    vd = cfg["verdict"]
    direction = str(getattr(advice, "direction", "") or "").lower()
    if decision.verdict == "veto":
        verdict = "reject"
        score = min(score, float(cfg["risk"]["veto_score_cap"]))
    elif decision.verdict == "caution":
        verdict = "watch"
    elif score >= vd["buy_min"] and direction in {"buy", "add", "long"}:
        verdict = "buy"
    elif score >= vd["watch_min"]:
        verdict = "watch"
    else:
        verdict = "reject"

    evidence = []
    for ev in (report.evidence or [])[:5]:
        evidence.append({
            "idx": ev.idx, "title": ev.title, "source": ev.source,
            "publish_time": ev.publish_time, "sentiment": ev.sentiment,
            "confidence": ev.confidence, "url": ev.url,
            "retrieval": ev.retrieval, "snippet": (ev.snippet or "")[:120],
        })

    # 价格换算：三 Agent 内部全程用前复权(hfq)，前端展示必须是不复权真实价
    factor = _factor_at(store, code, as_of)
    close_raw = _to_raw(snap.get("close"), factor)
    return {
        "code": code,
        "as_of": as_of,
        "score": score,
        "verdict": verdict,
        "parts": parts,
        "gate": decision.to_dict(),
        "sentiment": report.sentiment,
        "impact": report.impact,
        "summary": (report.summary or "")[:300],
        "direction": direction,
        "position_pct": getattr(advice, "position_pct", None),
        "entry": _to_raw(getattr(advice, "entry", None), factor) or close_raw,
        "stop_loss": _to_raw(getattr(advice, "stop_loss", None), factor),
        "take_profit": _to_raw(getattr(advice, "take_profit", None), factor),
        "holding_period": getattr(advice, "holding_period", ""),
        "rationale": (getattr(advice, "rationale", "") or "")[:400],
        "risk_notes": (getattr(advice, "risk_notes", "") or "")[:200],
        "close": close_raw,
        "entry_from_close": _to_raw(getattr(advice, "entry", None), factor) is None,
        "close_hfq": snap.get("close"),
        "hfq_factor": factor,
        "ret_20d": snap.get("ret_20d"),
        "vol_ratio": snap.get("vol_ratio"),
        "atr_pct": snap.get("atr_pct"),
        "evidence": evidence,
    }


# ----------------------------------------------------------------------
#  主流程
# ----------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="每日推荐股（新闻闸门 + 综合打分）")
    ap.add_argument("--as-of", default=None, help="分析基准日 YYYY-MM-DD（缺省=库内最新交易日）")
    ap.add_argument("--limit", type=int, default=20, help="候选池最多分析几只（默认 20）")
    ap.add_argument("--pool-turnover", type=int, default=60, help="候选池：近20日成交额 TopN")
    ap.add_argument("--pool-move", type=int, default=20, help="候选池：当日涨跌幅 TopM")
    ap.add_argument("--codes", default=None, help="只分析指定代码，逗号分隔")
    ap.add_argument("--out-dir", default=None, help="输出目录（缺省 data/signals）")
    ap.add_argument("--dry-run", action="store_true", help="不写文件，只打印")
    ap.add_argument("--llm", choices=["none", "eco", "max"], default="none",
                    help="研判档位：none=纯规则（默认，最快）；eco=仅 Analyst 用大模型；max=三层都用")
    ap.add_argument("--no-live", action="store_true",
                    help="关闭 L2 现场新闻抓取（当 as_of 不是今天时本来就会自动跳过）")
    args = ap.parse_args()

    t0 = time.time()
    cfg = load_config()
    gate = NewsGate(
        min_sentiment=cfg["gate"]["min_sentiment"],
        caution_sentiment=cfg["gate"]["caution_sentiment"],
        min_evidence=int(cfg["gate"]["min_evidence"]),
    )
    store = MarketStore()

    # 大模型档位：none=纯规则；eco=仅 Analyst；max=三层（慢）
    llm = None
    scope = "all"
    tier = args.llm
    if args.llm != "none":
        from src.models.llm import get_backend
        logger.info("加载本地大模型（4bit），首次约 15 秒…")
        llm = get_backend("qwen")
        if hasattr(llm, "load"):
            llm.load()
        scope = "all" if args.llm == "max" else "analyst"
        logger.info("研判档位 = %s（llm_scope=%s）", args.llm, scope)

    orch = Orchestrator(live_news=not args.no_live, llm=llm, llm_scope=scope)
    names = load_names()
    out_dir = Path(args.out_dir) if args.out_dir else SIGNAL_DIR

    # 基准日
    if args.as_of:
        as_of = args.as_of
    else:
        df_last = store.daily_latest("sh.600519", adjust="raw", n=1)
        as_of = str(df_last["date"].iloc[0])[:10] if len(df_last) else date.today().isoformat()
    logger.info("基准日 %s，配置 %s", as_of, json.dumps(cfg["weights"], ensure_ascii=False))

    # 候选池
    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        import duckdb
        con = duckdb.connect(str(store.db_path), read_only=True)
        try:
            six = build_daily_pool(con, top_turnover=args.pool_turnover,
                                   top_move=args.pool_move, as_of=as_of)
        finally:
            con.close()
        codes = [f"{'sh' if s.startswith(('6', '9')) else 'sz'}.{s}" for s in six]
    codes = codes[: args.limit]
    logger.info("候选 %d 只: %s", len(codes), "、".join(codes[:8]) + ("…" if len(codes) > 8 else ""))

    items: List[Dict[str, Any]] = []
    for i, code in enumerate(codes, 1):
        try:
            rec = analyze_one(orch, gate, store, code, as_of, cfg)
            rec["name"] = names.get(code.split(".")[-1], "") or names.get(code, "")
            items.append(rec)
            logger.info("[%d/%d] %s 分 %.1f → %s（闸门 %s: %s）", i, len(codes), code,
                        rec["score"], rec["verdict"], rec["gate"]["verdict"],
                        rec["gate"]["reason"][:40])
        except Exception as e:  # noqa: BLE001
            logger.warning("[%d/%d] %s 分析失败: %s", i, len(codes), code,
                           f"{type(e).__name__}: {str(e)[:120]}")

    items.sort(key=lambda x: (x["verdict"] != "buy", -x["score"]))
    summary = {v: sum(1 for it in items if it["verdict"] == v)
               for v in ("buy", "watch", "reject")}
    payload = {
        "as_of": as_of,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "n_candidates": len(codes),
        "llm_tier": tier,
        "weights": cfg["weights"],
        "summary": summary,
        "items": items,
    }

    if args.dry_run:
        logger.info("dry-run：%d 条结果未写盘，用时 %.1fs", len(items), time.time() - t0)
        for it in items[:10]:
            print(f"  {it['code']} {it['name']:<6} 分 {it['score']:5.1f} {it['verdict']:<6} "
                  f"{it['gate']['verdict']:<8} {it['gate']['reason'][:40]}")
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{as_of}.json"
    out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # 历史流水（便于月度统计闸门有效性）
    hist = out_dir / "history.csv"
    rows = [{
        "as_of": as_of, "code": it["code"], "score": it["score"],
        "verdict": it["verdict"], "gate": it["gate"]["verdict"],
        "close": it["close"], "sentiment": it["sentiment"],
        "impact": it["impact"],
    } for it in items]
    pd.DataFrame(rows).to_csv(hist, mode="a", header=not hist.exists(), index=False, encoding="utf-8-sig")

    logger.info("已写入 %s（buy %d / watch %d / reject %d），用时 %.1fs",
                out_file, summary["buy"], summary["watch"], summary["reject"], time.time() - t0)


def _num(v) -> Optional[float]:
    """NaN / None / 非数 → None。"""
    try:
        f = float(v)
        return None if pd.isna(f) else f
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    main()
