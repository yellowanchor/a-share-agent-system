# -*- coding: utf-8 -*-
"""fetch_stock_names.py — 抓股票名称表（6 位代码 → 名称）
================================================================

数据源是腾讯行情快照接口 ``https://qt.gtimg.cn/q=sh600519,sz000001,...``，
一次可带几十只，返回::

    v_sh600519="1~贵州茅台~600519~1291.50~...";

名称在 split("~")[1]。抓完写 ``data/reference/stock_names.json``，
``run_daily_signals.py`` 与前端都从这里取名字（缺失时显示代码）。

用法::

    python scripts/fetch_stock_names.py                # 全库股票
    python scripts/fetch_stock_names.py --limit 20     # 先试 20 只
    python scripts/fetch_stock_names.py --codes 600519,000001
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.request
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import duckdb  # noqa: E402

from src.data.collect.market_fetch import set_pace  # noqa: E402

QT_API = "https://qt.gtimg.cn/q="
DB_PATH = ROOT / "data" / "processed" / "market_data.duckdb"
OUT_PATH = ROOT / "data" / "reference" / "stock_names.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("names")


def _prefix(six: str) -> str:
    """6 位代码 → 腾讯前缀（sh / sz）。"""
    return "sh" if six.startswith(("6", "9", "5")) else "sz"


def _http_gbk(url: str, timeout: int = 10) -> str:
    """腾讯 qt 接口返回 **GBK** 编码（不是 UTF-8），必须单独解码，否则中文全乱码。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://gu.qq.com/",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("gbk", "ignore")


def fetch_names(sixes: List[str], batch: int = 60, timeout: int = 10) -> Dict[str, str]:
    """批量取名称；解析失败的批次跳过（不抛异常）。"""
    out: Dict[str, str] = {}
    for i in range(0, len(sixes), batch):
        chunk = sixes[i: i + batch]
        url = QT_API + ",".join(f"{_prefix(s)}{s}" for s in chunk)
        for attempt in range(3):
            try:
                raw = _http_gbk(url, timeout=timeout)
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    logger.warning("批次 %d 失败: %s", i // batch + 1, str(e)[:100])
                    raw = None
                else:
                    time.sleep(1.5 * (attempt + 1))
        if raw is None:
            continue
        if not isinstance(raw, str):
            logger.warning("批次 %d 返回非文本（%s）", i // batch + 1, type(raw).__name__)
            continue
        for line in raw.split(";"):
            line = line.strip()
            if not line.startswith("v_") or '="' not in line:
                continue
            body = line.split('="', 1)[1].rstrip('"')
            parts = body.split("~")
            if len(parts) < 3:
                continue
            name, six = parts[1].strip(), parts[2].strip()
            if name and six:
                out[six] = name
        logger.info("批次 %d: %d 只 → 累计 %d", i // batch + 1, len(chunk), len(out))
        time.sleep(0.2)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="抓股票名称表")
    ap.add_argument("--codes", default=None, help="只抓指定 6 位代码（逗号分隔）")
    ap.add_argument("--limit", type=int, default=0, help="只抓前 N 只（0=全部）")
    ap.add_argument("--batch", type=int, default=60, help="每批只数")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--out", default=str(OUT_PATH))
    args = ap.parse_args()
    set_pace(0.0)

    if args.codes:
        sixes = [c.strip().split(".")[-1] for c in args.codes.split(",") if c.strip()]
    else:
        con = duckdb.connect(args.db, read_only=True)
        try:
            rows = con.execute(
                "SELECT DISTINCT regexp_extract(code, '([0-9]{6})$', 1) AS six "
                "FROM daily_raw ORDER BY six"
            ).fetchall()
        finally:
            con.close()
        sixes = [r[0] for r in rows if r and r[0]]
    if args.limit:
        sixes = sixes[: args.limit]
    logger.info("待抓 %d 只", len(sixes))

    names = fetch_names(sixes, batch=args.batch)
    if not names:
        logger.error("一条都没抓到，未覆盖写旧文件")
        return

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    old = {}
    if out_path.exists():  # 增量合并，避免只跑 --limit 时把全表覆盖成几条
        try:
            old = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            old = {}
    old.update(names)
    out_path.write_text(json.dumps(old, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
    logger.info("已写入 %s（本次 %d，累计 %d）", out_path, len(names), len(old))


if __name__ == "__main__":
    main()
