# -*- coding: utf-8 -*-
"""
脚本 06：数据集合并 + 质量校验 + 报告
====================================
1. 将 raw 分片合并为 processed 最终数据集
2. 对各数据集做完整性/覆盖度/异常值校验
3. 输出 quality_report.json + quality_report.md

用法: python scripts/06_validate_report.py
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from src.storage import ensure_dirs, RAW_DIR, PROCESSED_DIR, load_shards  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

DATASETS = ["daily_hfq", "daily_raw", "financials_yjbb", "news"]


def merge_all() -> None:
    """合并所有分片为 processed 最终文件"""
    for name in DATASETS:
        if name == "news" and (PROCESSED_DIR / "news.parquet").exists():
            # 05b 已生成去重后的最终版（SimHash + url/text 去重），跳过重合并
            logger.info("news 已存在去重最终版，跳过重合并")
            continue
        parts = sorted(RAW_DIR.glob(f"{name}_part*.parquet"))
        if not parts:
            logger.warning("数据集 %s 无分片，跳过", name)
            continue
        df = load_shards(name, subdir="raw")
        if df.empty:
            continue
        out = PROCESSED_DIR / f"{name}.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(out, index=False)
        logger.info("合并完成: %s (%d 行)", out, len(df))


def validate_daily(df: pd.DataFrame, label: str) -> dict:
    """日线数据集校验"""
    if df.empty:
        return {"status": "EMPTY"}
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    n_stock = df["code"].nunique()
    per_stock = df.groupby("code").size()
    na_close = float(df["close"].isna().mean())
    # 复权价格合理性：后复权序列不允许负值/零值
    bad_price = float(((df["close"] <= 0) | (df["high"] < df["low"])).mean())
    return {
        "status": "OK",
        "rows": int(len(df)),
        "stocks": int(n_stock),
        "date_range": [df["date"].min().strftime("%Y-%m-%d"),
                       df["date"].max().strftime("%Y-%m-%d")],
        "rows_per_stock": {"min": int(per_stock.min()),
                           "median": float(per_stock.median()),
                           "max": int(per_stock.max())},
        "na_close_ratio": round(na_close, 6),
        "bad_price_ratio": round(bad_price, 6),
        "size_mb": round(_file_size(PROCESSED_DIR / f"{label}.parquet"), 2),
    }


def validate_financials(df: pd.DataFrame) -> dict:
    """财务数据集校验"""
    if df.empty:
        return {"status": "EMPTY"}
    return {
        "status": "OK",
        "rows": int(len(df)),
        "stocks": int(df["code"].nunique()),
        "periods": int(df["report_date"].nunique()),
        "period_range": [df["report_date"].min(), df["report_date"].max()],
        "pub_date_range": [str(df["pub_date"].min()), str(df["pub_date"].max())],
        "na_roe_ratio": round(float(df["roe"].isna().mean()), 6),
        "size_mb": round(_file_size(PROCESSED_DIR / "financials_yjbb.parquet"), 2),
    }


def validate_news(df: pd.DataFrame) -> dict:
    """新闻数据集校验"""
    if df.empty:
        return {"status": "EMPTY"}
    return {
        "status": "OK",
        "rows": int(len(df)),
        "stocks": int(df["stock_code"].nunique()),
        "time_range": [str(df["publish_time"].min()), str(df["publish_time"].max())],
        "avg_len": int(df["text"].str.len().mean()),
        "size_mb": round(_file_size(PROCESSED_DIR / "news.parquet"), 2),
    }


def _file_size(p: Path) -> float:
    return p.stat().st_size / (1024 * 1024) if p.exists() else 0.0


def main() -> None:
    ensure_dirs()
    merge_all()

    report: dict[str, dict] = {}
    for name in DATASETS:
        out = PROCESSED_DIR / f"{name}.parquet"
        if not out.exists():
            report[name] = {"status": "MISSING"}
            continue
        df = pd.read_parquet(out)
        if name.startswith("daily"):
            report[name] = validate_daily(df, name)
        elif name == "financials_yjbb":
            report[name] = validate_financials(df)
        elif name == "news":
            report[name] = validate_news(df)

    # 输出 JSON
    json_path = PROCESSED_DIR / "quality_report.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # 输出 Markdown 报告
    md = _render_markdown(report)
    md_path = PROCESSED_DIR / "quality_report.md"
    md_path.write_text(md, encoding="utf-8")

    logger.info("质量报告已生成:\n  %s\n  %s", json_path, md_path)
    print(md)


def _render_markdown(report: dict) -> str:
    lines = ["# 数据集质量报告", ""]
    for name, r in report.items():
        lines.append(f"## {name}")
        if r.get("status") != "OK":
            lines.append(f"- 状态: **{r.get('status')}**")
            lines.append("")
            continue
        lines.append(f"- 状态: OK")
        lines.append(f"- 记录数: **{r['rows']:,}**")
        if "stocks" in r:
            lines.append(f"- 覆盖股票: {r['stocks']}")
        if "date_range" in r:
            lines.append(f"- 日期范围: {r['date_range'][0]} ~ {r['date_range'][1]}")
        if "rows_per_stock" in r:
            rps = r["rows_per_stock"]
            lines.append(f"- 每股记录数: min={rps['min']} median={rps['median']} max={rps['max']}")
        if "na_close_ratio" in r:
            lines.append(f"- 收盘价缺失率: {r['na_close_ratio']:.4%}")
        if "bad_price_ratio" in r:
            lines.append(f"- 异常价格比例(close<=0 或 high<low): {r['bad_price_ratio']:.4%}")
        if "periods" in r:
            lines.append(f"- 报告期数: {r['periods']} ({r['period_range'][0]}~{r['period_range'][1]})")
            lines.append(f"- 公告日期范围: {r['pub_date_range'][0]} ~ {r['pub_date_range'][1]}")
        if "time_range" in r:
            lines.append(f"- 新闻时间范围: {r['time_range'][0]} ~ {r['time_range'][1]}")
        if "size_mb" in r:
            lines.append(f"- 文件大小: {r['size_mb']} MB")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
