# -*- coding: utf-8 -*-
"""market_fetch.py — 日线增量抓取（不复权源 + 后复权因子递推）
====================================================================

数据源策略（2026-11-23 实测）：

* **腾讯 fqkline 为主源**：``web.ifzq.gtimg.cn``，单只一次请求返回不复权日线，
  另一次返回前复权日线；8 线程并发 5443 只约 2–4 分钟，稳定不断连。
  字段顺序为 ``[日期, 开, 收, 高, 低, 成交量(手)]``。
* **东方财富为备源**：``push2his.eastmoney.com`` 字段更全（成交额、换手、涨跌幅、
  前收盘），但同一 IP 高频请求会被断连（``curl: (56) Connection closed abruptly``），
  实测 5443 只 × 8 线程触发封禁，之后连串行请求也失败。故只作降级备源。
* baostock 不可用（``login: 10002007 网络接收错误``）；本机 anaconda 无 akshare。

前收盘怎么算（关键）：

    腾讯不复权序列在除权日会跳空，直接取"上一根收盘价"当 preclose 会算错涨跌幅。
    同时抓前复权序列后，用除权连续性反推：

        preclose_t = raw_close_t × qfq_close_{t-1} / qfq_close_t

    因为前复权序列满足 ``qfq_t / qfq_{t-1} = raw_close_t / preclose_t``。

后复权因子怎么递推（实测校验过）：

    ``daily_hfq = daily_raw × hfq_factor``，因子只在除权日跳变，且

        factor_t = factor_{t-1} × raw_close_{t-1} / preclose_t

    用 600519 全历史 6046 个交易日验证：29 个除权日的因子跳变比例与该式
    吻合到小数点后 6 位；``daily_hfq = daily_raw × hfq_factor`` 在 1755 万行上
    零误差。

字段缺口（腾讯源）：成交额、换手率、PE/PB 无逐日数据 → 置 ``NULL``，
``source`` 列写 ``tencent`` 以便审计；成交额在选池时用
``volume × (high+low+close)/3`` 现算代理（见 ``pool.py``）。

用法::

    bars = fetch_raw_bars("sh.600519", "20260821", "20261123")
    raw_rows, hfq_rows = to_db_rows("sh.600519", bars, 1291.5, 8.8825126710204)
"""
from __future__ import annotations

import logging
import time
import urllib.parse
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- 数据源常量

TENCENT_API = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
EM_KLINE_API = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
# f51 日期 f52 开 f53 收 f54 高 f55 低 f56 量 f57 额 f58 振幅 f59 涨跌幅 f60 涨跌额 f61 换手
_FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")

# 除权除息判定：参考价与前收盘的偏离超过 max(0.011 元, 0.2%)
_EXDIV_ABS = 0.011
_EXDIV_REL = 0.002

# 请求间隔闸门（实测教训：东财 8 线程突发 5443 次 → 整个 IP 被封约半小时；
# 腾讯同样会在高频突发后限流。故所有请求走全局节流，默认 0.15 秒/次）
_PACE_LOCK = None
_PACE_LAST = [0.0]
_PACE_INTERVAL = 0.15


def set_pace(interval: float) -> None:
    """设置全局请求最小间隔（秒）。0 表示不节流。"""
    global _PACE_INTERVAL
    _PACE_INTERVAL = max(0.0, float(interval))


def _throttle() -> None:
    """按全局间隔等待，保证同一进程内所有抓取请求被均匀摊开。"""
    import threading
    global _PACE_LOCK
    if _PACE_INTERVAL <= 0:
        return
    if _PACE_LOCK is None:
        _PACE_LOCK = threading.Lock()
    with _PACE_LOCK:
        now = time.monotonic()
        wait = _PACE_INTERVAL - (now - _PACE_LAST[0])
        if wait > 0:
            time.sleep(wait)
        _PACE_LAST[0] = time.monotonic()


# ---------------------------------------------------------------- 代码格式

def tencent_code(code: str) -> str:
    """``sh.600519`` / ``600519`` → ``sh600519``。"""
    if "." in code:
        prefix, num = code.split(".", 1)
    else:
        num = code
        prefix = "sh" if num.startswith(("6", "9")) else "sz"
    return f"{prefix.lower()}{num}"


def secid(code: str) -> str:
    """``sh.600519`` → ``1.600519``（东财备源用）。"""
    if "." in code:
        prefix, num = code.split(".", 1)
    else:
        num = code
        prefix = "sh" if num.startswith(("6", "9")) else "sz"
    return f"{'1' if prefix.lower() == 'sh' else '0'}.{num}"


# ---------------------------------------------------------------- 熔断器
# 实测教训：数据源在突发后返回 HTTP 501 / 直接断连时，单只失败是"空结果"而不是
# 异常，若继续循环 5000 次会空转半小时。这里记录连续失败次数，超过阈值直接抛
# SourceBlocked，让上层停下来等冷却。

class SourceBlocked(RuntimeError):
    """连续多次抓取失败，判定数据源已限流/封禁，应立即中止本轮任务。"""


_FAIL_STREAK = [0]
_MAX_FAIL_STREAK = 25

# 单源熔断：某源连续失败 3 次后，本轮任务内不再尝试它（避免 5443 只每只都
# 先花 10 秒等它超时）。冷却期过后自动恢复尝试。
_SRC_FAIL = {"tencent": 0, "sina": 0, "em": 0}
_SRC_DOWN_UNTIL = {"tencent": 0.0, "sina": 0.0, "em": 0.0}
_SRC_COOLDOWN = 900.0


def _source_available(src: str) -> bool:
    return time.monotonic() >= _SRC_DOWN_UNTIL.get(src, 0.0)


def _note_source(src: str, ok: bool) -> None:
    if ok:
        _SRC_FAIL[src] = 0
        return
    _SRC_FAIL[src] = _SRC_FAIL.get(src, 0) + 1
    if _SRC_FAIL[src] >= 3 and _source_available(src):
        _SRC_DOWN_UNTIL[src] = time.monotonic() + _SRC_COOLDOWN
        logger.warning("数据源 %s 连续失败 %d 次，熔断 %.0f 分钟（本轮改用备源）",
                       src, _SRC_FAIL[src], _SRC_COOLDOWN / 60)


def _note_result(ok: bool) -> None:
    if ok:
        _FAIL_STREAK[0] = 0
        return
    _FAIL_STREAK[0] += 1
    if _FAIL_STREAK[0] >= _MAX_FAIL_STREAK:
        raise SourceBlocked(
            f"连续 {_FAIL_STREAK[0]} 次抓取失败：数据源已限流（常见返回 HTTP 501 / 连接被断开），"
            f"请等 30–60 分钟冷却后重跑，并用 --pace 调大请求间隔"
        )


def reset_streak() -> None:
    """手动清零熔断计数（新一轮任务开始前调用）。"""
    _FAIL_STREAK[0] = 0


# ---------------------------------------------------------------- 腾讯主源

def _http_json(url: str, timeout: int = 10, referer: str = "https://gu.qq.com/"):
    _throttle()
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Referer": referer})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def _tencent_series(tc: str, beg: str, end: str, fq: str,
                    timeout: int, retries: int) -> tuple[list[list[str]], str]:
    """抓腾讯单只日线序列；返回 ``(rows, 股票名)``，rows 为原始字符串数组。"""
    b, e = _ymd(beg), _ymd(end)
    url = (f"{TENCENT_API}?param={tc},day,{b},{e},640,{fq}")
    for attempt in range(retries):
        try:
            js = _json_loads(_http_json(url, timeout=timeout))
            node = (js.get("data") or {}).get(tc) or {}
            key = "qfqday" if fq == "qfq" else "day"
            rows = node.get(key) or node.get("day") or []
            name = ""
            qt = node.get("qt")
            if isinstance(qt, dict):
                arr = qt.get(tc)
                if isinstance(arr, list) and len(arr) > 1:
                    name = str(arr[1])
            rows = [r for r in rows if isinstance(r, list) and len(r) >= 6]
            ok = bool(rows or name)
            _note_source("tencent", ok)
            _note_result(ok)
            return rows, name
        except Exception as e:  # noqa: BLE001
            if attempt >= retries - 1:
                logger.debug("腾讯K线失败 %s: %s", tc, str(e)[:80])
                _note_source("tencent", False)
                _note_result(False)
                return [], ""
            time.sleep(2.0 * (attempt + 1))
    return [], ""


def fetch_tencent_bars(code: str, beg: str, end: str,
                       timeout: int = 10, retries: int = 3) -> dict:
    """取腾讯不复权 + 前复权两套序列，并算好每日 ``preclose``。

    Returns:
        ``{"bars": [...], "name": str}``，bars 按日期升序，字段
        ``date/open/high/low/close/preclose/volume/pct_chg``（volume 已换算成「股」）。
    """
    tc = tencent_code(code)
    raw_rows, name = _tencent_series(tc, beg, end, "", timeout, retries)
    if not raw_rows:
        return {"bars": [], "name": name}
    qfq_rows, _ = _tencent_series(tc, beg, end, "qfq", timeout, retries)

    raw_parsed = [_tencent_row(r) for r in raw_rows]
    qfq_close: list[Optional[float]] = []
    if len(qfq_rows) == len(raw_rows):
        qfq_close = [_f(r[2]) for r in qfq_rows]      # [日期, 开, 收, 高, 低, 量]
    else:  # 长度不一致说明两套序列跨了不同的交易日，放弃用前复权反推
        logger.debug("腾讯前复权长度不一致 %s: %d vs %d", tc, len(qfq_rows), len(raw_rows))
        qfq_close = [None] * len(raw_rows)

    bars: list[dict] = []
    for i, b in enumerate(raw_parsed):
        if b is None:
            continue
        close = b["close"]
        preclose: Optional[float] = None
        if i > 0:
            prev = _f(raw_rows[i - 1][2])
            q_prev, q_now = (qfq_close[i - 1], qfq_close[i]) if qfq_close else (None, None)
            if prev is not None and q_prev and q_now:
                preclose = round(close * q_prev / q_now, 4)
            elif prev is not None:
                preclose = prev
        pct = None if not preclose else round((close / preclose - 1) * 100, 4)
        bars.append({
            "date": b["date"],
            "open": b["open"],
            "high": b["high"],
            "low": b["low"],
            "close": close,
            "preclose": preclose,
            "volume": None if b["volume"] is None else round(b["volume"] * 100.0, 2),  # 手 → 股
            "amount": None,
            "turn": None,
            "pct_chg": pct,
        })
    return {"bars": bars, "name": name}


def _tencent_row(r: list[str]) -> Optional[dict]:
    """``[日期, 开, 收, 高, 低, 量(手)]`` → dict。"""
    if len(r) < 6:
        return None
    return {
        "date": str(r[0])[:10],
        "open": _f(r[1]),
        "close": _f(r[2]),
        "high": _f(r[3]),
        "low": _f(r[4]),
        "volume": _f(r[5]),
    }


# ---------------------------------------------------------------- 东财备源

def fetch_em_bars(code: str, beg: str, end: str,
                  timeout: int = 15, retries: int = 3) -> dict:
    """东财不复权日线（字段更全；高频会被断连，仅作降级）。"""
    from curl_cffi import requests as _requests

    params = {
        "secid": secid(code),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": _FIELDS2,
        "klt": "101",
        "fqt": "0",
        "beg": _ymd(beg),
        "end": _ymd(end),
    }
    headers = {"user-agent": _UA, "referer": "https://quote.eastmoney.com/"}
    for attempt in range(retries):
        try:
            _throttle()
            r = _requests.get(EM_KLINE_API, params=params, headers=headers,
                              timeout=timeout, impersonate="chrome")
            data = (r.json() or {}).get("data")
            if not data:
                return {"bars": [], "name": data.get("name") if data else ""}
            bars = []
            for line in data.get("klines") or []:
                p = line.split(",")
                if len(p) < 11:
                    continue
                close, chg = _f(p[2]), _f(p[9])
                bars.append({
                    "date": p[0], "open": _f(p[1]), "close": close,
                    "high": _f(p[3]), "low": _f(p[4]),
                    "volume": _f(p[5]), "amount": _f(p[6]),
                    "pct_chg": _f(p[8]),
                    "preclose": None if (close is None or chg is None) else round(close - chg, 4),
                    "turn": _f(p[10]),
                })
            return {"bars": bars, "name": str(data.get("name") or "")}
        except Exception as e:  # noqa: BLE001
            if attempt >= retries - 1:
                logger.debug("东财K线失败 %s: %s", code, str(e)[:80])
                return {"bars": [], "name": ""}
            time.sleep(2.0 * (attempt + 1))
    return {"bars": [], "name": ""}


# ---------------------------------------------------------------- 新浪备源

SINA_API = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "CN_MarketData.getKLineData")


def fetch_sina_bars(code: str, beg: str, end: str,
                    timeout: int = 10, retries: int = 2) -> dict:
    """新浪不复权日线（只有 OHLCV，volume 单位＝股；一次最多给最近 1023 根）。"""
    tc = tencent_code(code)
    params = {"symbol": tc, "scale": "240", "ma": "no", "datalen": "1023"}
    url = SINA_API + "?" + urllib.parse.urlencode(params)
    b, e = _ymd(beg), _ymd(end)
    for attempt in range(retries):
        try:
            arr = _json_loads(_http_json(url, timeout=timeout,
                                         referer="https://finance.sina.com.cn/"))
            if not isinstance(arr, list) or not arr:
                _note_source("sina", False)
                _note_result(False)
                return {"bars": [], "name": ""}
            rows = []
            for it in arr:
                if not isinstance(it, dict):
                    continue
                d = str(it.get("day") or "")[:10]
                if not d or d < b or d > e:
                    continue
                rows.append(it)
            rows.sort(key=lambda x: str(x.get("day"))[:10])
            bars = []
            prev_close = None
            for it in rows:
                close = _f(it.get("close"))
                preclose = prev_close
                pct = None if (not preclose or close is None) else round((close / preclose - 1) * 100, 4)
                bars.append({
                    "date": str(it.get("day"))[:10],
                    "open": _f(it.get("open")),
                    "high": _f(it.get("high")),
                    "low": _f(it.get("low")),
                    "close": close,
                    "preclose": preclose,
                    "volume": _f(it.get("volume")),
                    "amount": None,
                    "turn": None,
                    "pct_chg": pct,
                })
                prev_close = close
            _note_source("sina", True)
            _note_result(True)
            return {"bars": bars, "name": ""}
        except Exception as e:  # noqa: BLE001
            if attempt >= retries - 1:
                logger.debug("新浪K线失败 %s: %s", code, str(e)[:80])
                _note_source("sina", False)
                _note_result(False)
                return {"bars": [], "name": ""}
            time.sleep(2.0 * (attempt + 1))
    return {"bars": [], "name": ""}


# ---------------------------------------------------------------- 统一入口

def fetch_raw_bars(code: str, beg: str, end: str, timeout: int = 10,
                   retries: int = 3, source: str = "auto") -> list[dict]:
    """抓不复权日线（自动选源）。

    Args:
        code: ``sh.600519`` / ``600519``。
        beg/end: ``YYYYMMDD`` 或 ``YYYY-MM-DD``。
        source: ``auto`` 先腾讯后东财；``tencent`` / ``em`` 强制指定。

    Returns:
        按日期升序的 ``[{date, open, high, low, close, preclose, volume,
        amount, turn, pct_chg, name}]``；无数据返回空列表。
    """
    order = {"auto": ("tencent", "sina", "em"), "tencent": ("tencent",),
             "sina": ("sina",), "em": ("em",)}.get(source, ("tencent", "sina", "em"))
    for src in order:
        if not _source_available(src):      # 已在熔断冷却期，跳过
            continue
        if src == "tencent":
            out = fetch_tencent_bars(code, beg, end, timeout=timeout, retries=retries)
        elif src == "sina":
            out = fetch_sina_bars(code, beg, end, timeout=timeout, retries=max(1, retries - 1))
        else:
            out = fetch_em_bars(code, beg, end, timeout=max(timeout, 15), retries=retries)
        bars = out.get("bars") or []
        if bars:
            name = out.get("name") or ""
            for b in bars:
                b["name"] = name
                b["extra_source"] = src
            return bars
    return []


def to_db_rows(
    code: str,
    bars: list[dict],
    base_close: Optional[float],
    base_factor: Optional[float],
) -> tuple[list[dict], list[dict]]:
    """把抓取结果转成 ``daily_raw`` / ``daily_hfq`` 两张表的行。

    Args:
        code: 带市场前缀的代码，如 ``sh.600519``。
        bars: :func:`fetch_raw_bars` 的返回值（升序）。
        base_close: 库中该股**最后一个交易日**的原始收盘价。
        base_factor: 库中该股最后一个交易日的 ``hfq_factor``。

    Returns:
        ``(raw_rows, hfq_rows)``，字段顺序与 DuckDB 表一致（19 列）。
    """
    factor = float(base_factor) if base_factor else 1.0
    prev_close = float(base_close) if base_close else None
    raw_rows: list[dict] = []
    hfq_rows: list[dict] = []

    for b in bars:
        close = b["close"]
        if close is None:
            continue
        preclose = b.get("preclose")
        if preclose is None and prev_close:
            preclose = prev_close

        # 1) 除权除息：参考价与上一日收盘不一致 → 修正后复权因子
        if prev_close and preclose and prev_close > 0:
            gap = abs(preclose - prev_close)
            if gap >= max(_EXDIV_ABS, prev_close * _EXDIV_REL):
                step = prev_close / preclose
                if 0.5 < step < 1.5:  # 防御：只接受合理量级的调整
                    factor = round(factor * step, 10)
                    logger.info("除权除息 %s %s: 因子 %.10f → %.10f",
                                code, b["date"], factor / step, factor)
                else:
                    logger.warning("疑似除权但比例异常 %s %s: %.4f", code, b["date"], step)

        name = str(b.get("name") or "")
        is_st = 1 if ("ST" in name.upper()) else 0

        # 2) 组装 raw 行（与 daily_raw 列顺序一致）
        raw = {
            "date": b["date"],
            "code": code,
            "open": b["open"],
            "high": b["high"],
            "low": b["low"],
            "close": close,
            "preclose": preclose,
            "volume": b.get("volume"),
            "amount": b.get("amount"),
            "turn": b.get("turn"),
            "tradestatus": 1,
            "pct_chg": b.get("pct_chg"),
            "pe_ttm": None,
            "pb_mrq": None,
            "ps_ttm": None,
            "pcf_ncf_ttm": None,
            "is_st": is_st,
            "source": str(b.get("extra_source") or "tencent"),
            "hfq_factor": factor,
        }
        raw_rows.append(raw)

        # 3) 复权行：只有价格类字段乘因子，量额/换手/涨跌幅不动
        hfq = dict(raw)
        for col in ("open", "high", "low", "close", "preclose"):
            v = raw[col]
            hfq[col] = None if v is None else round(v * factor, 4)
        hfq_rows.append(hfq)

        prev_close = close

    return raw_rows, hfq_rows


# ---------------------------------------------------------------- 小工具

def _ymd(s: str) -> str:
    """``2026-08-21`` / ``20260821`` → ``2026-08-21``（腾讯参数格式）。"""
    s = str(s).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s[:10]


def _json_loads(text: str):
    import json
    return json.loads(text)


def _f(v) -> Optional[float]:
    """安全转 float：空串 / ``-`` / ``None`` 返回 None。"""
    try:
        s = str(v).strip()
        if not s or s in {"-", "--", "None", "nan"}:
            return None
        return float(s)
    except (TypeError, ValueError):
        return None
