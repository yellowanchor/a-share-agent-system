# -*- coding: utf-8 -*-
"""boards.py — A 股板块归类（按 6 位代码前缀推断）

A 股不同板块的涨跌停、开户门槛、估值口径都不一样，推荐列表里标清板块，
比只给一个代码有用得多。规则按交易所代码段：

    科创板  688 / 689                    涨跌停 ±20%，需 50 万 + 2 年
    创业板  300 / 301                    涨跌停 ±20%，需 10 万 + 2 年
    沪市主板 600 / 601 / 603 / 605        涨跌停 ±10%
    深市主板 000 / 001 / 002 / 003        涨跌停 ±10%（原中小板已并入）
    北交所  43 / 83 / 87 / 88 / 920       涨跌停 ±30%，门槛 50 万
"""

from __future__ import annotations

BOARD_ORDER = ["沪市主板", "深市主板", "创业板", "科创板", "北交所", "其他"]

_BOARD_CLS = {
    "沪市主板": "bd",
    "深市主板": "bd",
    "创业板": "blue",
    "科创板": "purple",
    "北交所": "warn",
    "其他": "bd",
}


def six(code: str) -> str:
    """把 sh.600519 / 600519.SH 之类统一成 6 位数字。"""
    digits = "".join(ch for ch in str(code) if ch.isdigit())
    return digits[-6:] if len(digits) >= 6 else digits.rjust(6, "0")


def board_of(code: str) -> str:
    """6 位代码 → 板块名（BOARD_ORDER 之一）。"""
    c = six(code)
    if c.startswith(("688", "689")):
        return "科创板"
    if c.startswith(("300", "301")):
        return "创业板"
    if c.startswith(("600", "601", "603", "605")):
        return "沪市主板"
    if c.startswith(("000", "001", "002", "003")):
        return "深市主板"
    if c.startswith(("43", "83", "87", "88", "920")):
        return "北交所"
    return "其他"


def board_cls(code: str) -> str:
    """板块对应的 pill 颜色类。"""
    return _BOARD_CLS.get(board_of(code), "neu")


def board_hint(board: str) -> str:
    """板块的一句话特征（放在提示里）。"""
    return {
        "科创板": "±20% 涨跌停 · 需 50 万资产 + 2 年经验",
        "创业板": "±20% 涨跌停 · 需 10 万资产 + 2 年经验",
        "北交所": "±30% 涨跌停 · 流动性弱、波动大",
        "沪市主板": "±10% 涨跌停 · 蓝筹为主",
        "深市主板": "±10% 涨跌停 · 成长与蓝筹混合",
    }.get(board, "")
