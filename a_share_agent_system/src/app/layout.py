# -*- coding: utf-8 -*-
"""layout.py — 看板外壳：固定顶栏、主题与背景、页脚、通用 HTML 渲染。

结构约定（"常规看板型"）：
    顶部：**通栏固定**的品牌条（图标 + 看板名 + 数据截止/建议数 + 外观 + 刷新），
          页面内容整体往下让出空间，滚动时这一条不跟着走；
    左侧：``st.navigation`` 生成的导航（每日建议 / 个股分析 / 模拟持仓 / 股市入门 / 数据更新）；
    中间：各页面的展示卡片。

主题与背景：
    ``data/ui/prefs.json`` 记住上一次的外观（主题 / 背景类型 / 模糊度），
    下次打开沿用；背景图片存在 ``src/app/static/`` 下，由 Streamlit 静态目录提供
    （``/app/static/bg.jpg``）。模糊度用 Pillow 预先把图片模糊好再交给 CSS，
    这样不需要靠滤镜叠层，滚动与覆盖范围都不会出问题。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import streamlit as st

from src.app import data, views

APP_DIR = Path(__file__).resolve().parent
UI_DIR = APP_DIR.parents[1] / "data" / "ui"
STATIC_DIR = APP_DIR / "static"
PREFS_FILE = UI_DIR / "prefs.json"
BG_SRC = STATIC_DIR / "bg_src.jpg"
BG_OUT = STATIC_DIR / "bg.jpg"

THEMES = ("light", "dark", "beige")
BACKGROUNDS = ("none", "grid", "photo")
DEFAULT_PREFS = {"theme": "light", "bg": "none", "blur": 6, "llm": "rules",
                "llm_tier": "max"}

_LOGO_SVG = (
    '<svg class="tb-logo" viewBox="0 0 40 40" xmlns="http://www.w3.org/2000/svg">'
    '<rect width="40" height="40" rx="11" fill="#0b1b33"/>'
    '<rect x="9" y="20" width="4" height="10" rx="1.2" fill="#f2b134"/>'
    '<rect x="8" y="17" width="6" height="2" rx="1" fill="#f2b134"/>'
    '<rect x="17" y="13" width="4" height="14" rx="1.2" fill="#7ec8ff"/>'
    '<rect x="16" y="10" width="6" height="2" rx="1" fill="#7ec8ff"/>'
    '<rect x="25" y="9" width="4" height="18" rx="1.2" fill="#f2b134"/>'
    '<rect x="24" y="6" width="6" height="2" rx="1" fill="#f2b134"/>'
    "</svg>"
)

_THEMES = {
    "light": {
        "page": "#f4f4f7", "card": "#ffffff", "text": "#1d1d1f",
        "muted": "#6e6e73", "line": "#e8e8ed", "side": "#fbfbfd",
        "overlay": "rgba(244,244,247,0.55)", "bar": "rgba(232,232,238,0.97)",
        "good_fg": "#d70015", "good_bg": "rgba(255,59,48,0.10)",
        "good_bd": "rgba(255,59,48,0.26)",
        "warn_fg": "#9a6700", "warn_bg": "rgba(255,159,10,0.14)",
        "warn_bd": "rgba(255,159,10,0.32)",
    },
    "dark": {
        "page": "#0d0d10", "card": "#191920", "text": "#f5f5f7",
        "muted": "#9a9aa2", "line": "#2b2b33", "side": "#141418",
        "overlay": "rgba(10,10,12,0.74)", "bar": "rgba(17,17,21,0.97)",
        "good_fg": "#ff7b72", "good_bg": "rgba(255,69,58,0.16)",
        "good_bd": "rgba(255,69,58,0.38)",
        "warn_fg": "#ffd166", "warn_bg": "rgba(255,193,7,0.14)",
        "warn_bd": "rgba(255,193,7,0.34)",
    },
    "beige": {
        "page": "#faf6ee", "card": "#fffdf7", "text": "#2c2620",
        "muted": "#7d7266", "line": "#e8ddcb", "side": "#f4ede0",
        "overlay": "rgba(250,246,238,0.62)", "bar": "rgba(243,236,224,0.97)",
        "good_fg": "#c2200f", "good_bg": "rgba(214,48,32,0.10)",
        "good_bd": "rgba(214,48,32,0.24)",
        "warn_fg": "#8a5a00", "warn_bg": "rgba(214,160,20,0.16)",
        "warn_bd": "rgba(214,160,20,0.32)",
    },
}

_BG_GRID = (
    "linear-gradient(rgba(0,0,0,0.035) 1px, transparent 1px),"
    "linear-gradient(90deg, rgba(0,0,0,0.035) 1px, transparent 1px)"
)


def md(html_str: str) -> None:
    """统一渲染 HTML 片段（unsafe_allow_html 由这里集中管理）。

    st.markdown 会把「行首 ≥4 空格缩进的行」解析成 markdown 代码块，
    导致 HTML 标签以原文形式泄漏，所以这里逐行 strip 并剔除空行。
    """
    cleaned = "\n".join(
        line.strip() for line in html_str.splitlines() if line.strip()
    )
    st.markdown(cleaned, unsafe_allow_html=True)


# ---------------------------------------------------------------- 偏好
def load_prefs() -> dict[str, Any]:
    prefs = dict(DEFAULT_PREFS)
    try:
        if PREFS_FILE.exists():
            prefs.update(json.loads(PREFS_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    return prefs


def save_prefs() -> None:
    prefs = {
        "theme": st.session_state.get("ui_theme", DEFAULT_PREFS["theme"]),
        "bg": st.session_state.get("ui_bg", DEFAULT_PREFS["bg"]),
        "blur": int(st.session_state.get("ui_blur", DEFAULT_PREFS["blur"])),
        "llm": st.session_state.get("ui_mode") or DEFAULT_PREFS["llm"],
        "llm_tier": st.session_state.get("ui_tier") or DEFAULT_PREFS["llm_tier"],
    }
    try:
        UI_DIR.mkdir(parents=True, exist_ok=True)
        PREFS_FILE.write_text(
            json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def init_state() -> None:
    """把上次保存的外观读进 session_state（页面首次渲染前调用一次）。"""
    prefs = load_prefs()
    st.session_state.setdefault("ui_theme", prefs["theme"])
    st.session_state.setdefault("ui_bg", prefs["bg"])
    st.session_state.setdefault("ui_blur", int(prefs["blur"]))
    st.session_state.setdefault("ui_mode", prefs.get("llm") or DEFAULT_PREFS["llm"])
    st.session_state.setdefault("ui_tier", prefs.get("llm_tier") or DEFAULT_PREFS["llm_tier"])


def blur_background(radius: int) -> None:
    """按模糊度把 bg_src.jpg 处理成 bg.jpg（没有原图就跳过）。"""
    if not BG_SRC.exists():
        return
    try:
        from PIL import Image, ImageFilter

        STATIC_DIR.mkdir(parents=True, exist_ok=True)
        img = Image.open(BG_SRC).convert("RGB")
        # 太大的图先缩小，模糊更快、页面也更轻
        img.thumbnail((1920, 1920))
        if radius and radius > 0:
            img = img.filter(ImageFilter.GaussianBlur(radius))
        img.save(BG_OUT, quality=88)
    except Exception:
        pass


def _bg_url() -> str:
    try:
        stamp = int(BG_OUT.stat().st_mtime)
    except OSError:
        stamp = 0
    return f"/app/static/bg.jpg?v={stamp}"


# ---------------------------------------------------------------- 主题
def _dark_extra() -> str:
    """深色模式额外覆盖：Streamlit 自带组件不会跟着换色，这里逐个压色。"""
    return """
    [data-testid="stSidebarNav"] a span,
    [data-testid="stSidebarNav"] a p { color: #f2f2f5 !important; }
    [data-testid="stSidebarNav"] a[aria-current="page"] { background: rgba(0,113,227,0.28) !important; }
    .tb-chip b, .tb-title, .tb-sub { color: #f5f5f7 !important; }
    .tb-sub { color: #a1a1aa !important; }
    .metrics .val, .metrics .lab, .metrics .val .unit { color: #f2f2f5 !important; }
    .metrics .lab { color: #9a9aa2 !important; }
    .metrics .cell { border-color: #2b2b33 !important; background: transparent !important; }
    .metrics, .metric { background: #191920 !important; border-color: #2b2b33 !important; }
    .stVerticalBlock, [data-testid="stVerticalBlockBorderWrapper"],
    [data-testid="stColumn"], [data-testid="stHorizontalBlock"] {
        background: transparent !important;
    }
    [data-testid="stPills"] button,
    [data-testid="stButtonGroup"] button,
    [data-baseweb="button-group"] button {
        background: #1e1e25 !important; color: #e6e6eb !important;
        border-color: #2f2f38 !important;
    }
    [data-testid="stPills"] button[aria-checked="true"],
    [data-testid="stPills"] button[aria-selected="true"],
    [data-testid="stButtonGroup"] button[aria-checked="true"],
    [data-testid="stButtonGroup"] button[aria-selected="true"],
    [data-baseweb="button-group"] button[aria-checked="true"] {
        background: rgba(0,113,227,0.45) !important; color: #ffffff !important;
        border-color: #0a84ff !important;
    }
    [data-testid="stBaseButton-secondary"],
    [data-testid="stPopoverButton"],
    [data-testid="stBaseButton-secondaryFormSubmit"],
    [data-testid="stBaseButton-primary"] {
        background: #202027 !important; color: #f2f2f5 !important;
        border-color: #34343f !important;
    }
    [data-testid="stBaseButton-secondary"]:hover,
    [data-testid="stPopoverButton"]:hover { border-color: #4a4a58 !important; }
    [data-testid="stExpander"] details {
        background: #17171d !important; border-color: #2b2b33 !important;
    }
    [data-testid="stExpander"] summary { color: #e6e6eb !important; }
    [data-testid="stAlert"] { background: #16202c !important; }
    [data-testid="stAlert"] p, [data-testid="stCaptionContainer"] { color: #d7dbe2 !important; }
    [data-baseweb="input"] input, [data-baseweb="input"] > div,
    [data-baseweb="select"] > div, [data-baseweb="textarea"] textarea {
        background: #1c1c22 !important; color: #f2f2f5 !important;
        border-color: #33333d !important;
    }
    /* 外观弹层跟随深色主题 */
    [data-testid="stPopoverBody"],
    [data-testid="stPopoverBody"] > div { background: #17171d !important;
        border-color: #2b2b33 !important; border-radius: 18px !important; }
    [data-testid="stPopoverBody"] [data-testid="stVerticalBlock"],
    [data-testid="stPopoverBody"] [data-testid="stVerticalBlockBorderWrapper"],
    [data-testid="stPopoverBody"] [data-testid="stElementContainer"],
    [data-testid="stPopoverBody"] [data-testid="stMarkdownContainer"] { background: transparent !important; }
    [data-testid="stPopoverBody"] [data-testid="stMarkdownContainer"] p,
    [data-testid="stPopoverBody"] [data-testid="stWidgetLabel"] p,
    [data-testid="stPopoverBody"] label,
    [data-testid="stPopoverBody"] [data-testid="stCaptionContainer"] p { color: #ececf1 !important; }
    [data-testid="stPopoverBody"] button { background: #23232b !important;
        border: 1px solid #3a3a45 !important; }
    [data-testid="stPopoverBody"] button p,
    [data-testid="stPopoverBody"] button span,
    [data-testid="stPopoverBody"] button::after { color: #f2f2f5 !important; }
    [data-testid="stFileUploaderDropzone"] { color: #c9c9d1 !important;
        background: #1c1c22 !important; }
    [data-testid="stFileUploaderDropzone"] button { background: #2a2a33 !important; }
    [data-testid="stPopoverBody"] [data-testid="stCaptionContainer"] p { color: #6e6e73 !important; }
    [data-testid="stFileUploaderDropzone"] { background: #1c1c22 !important; }
    [data-testid="stFileUploaderDropzone"] p,
    [data-testid="stFileUploaderDropzone"] span { color: #c9c9d1 !important; }

    """


def _theme_css(theme: str, bg: str) -> str:
    t = _THEMES.get(theme, _THEMES["light"])
    if bg == "grid":
        page_bg = (
            f"background-color:{t['page']} !important;"
            f"background-image:{_BG_GRID} !important;"
            "background-size:26px 26px !important;"
        )
        bar_bg = t["bar"]
    elif bg == "photo" and BG_OUT.exists():
        page_bg = (
            f"background-image:linear-gradient({t['overlay']},{t['overlay']}),"
            f"url('{_bg_url()}') !important;"
            "background-size:cover !important;"
            "background-position:center top !important;"
            "background-attachment:fixed !important;"
        )
        bar_bg = t["bar"]
    else:
        page_bg = f"background:{t['page']} !important;"
        bar_bg = t["page"]

    return f"""
    <style>
    :root {{
        --page-bg: {t['page']};
        --card-bg: {t['card']};
        --text: {t['text']};
        --muted: {t['muted']};
        --line: {t['line']};
        --bar-bg: {bar_bg};
    }}
    [data-testid="stApp"], [data-testid="stAppViewContainer"] {{ {page_bg} }}
    [data-testid="stSidebar"] {{ background: {t['side']} !important; }}
    body, [data-testid="stMarkdownContainer"], .tb-title,
    .pcard-name, .pcard-grid .pv, .pr-name, .stMarkdown p,
    .pt-name, .pt-cell .pv {{ color: {t['text']} !important; }}
    .tb-sub, .pcard-code, .pcard-grid .pk, .side-foot, .disclaimer,
    .pr-code, .pr-reason, .pr-score {{ color: {t['muted']} !important; }}
    .tb-chip b {{ color: {t['text']} !important; }}
    .tb-chip {{ background: {t['card']} !important; border-color: {t['line']} !important; }}
    /* 建议买入＝红（A 股涨色）、观察＝黄，深浅主题各用一套可读色 */
    .tb-chip.good {{ background: {t['good_bg']} !important;
        border-color: {t['good_bd']} !important; color: {t['good_fg']} !important; }}
    .tb-chip.good b {{ color: {t['good_fg']} !important; }}
    .tb-chip.warn {{ background: {t['warn_bg']} !important;
        border-color: {t['warn_bd']} !important; color: {t['warn_fg']} !important; }}
    .tb-chip.warn b {{ color: {t['warn_fg']} !important; }}
    .pcard, .metrics, .metric {{ background: {t['card']} !important;
        border-color: {t['line']} !important; }}
    .tb-rule {{ background: {t['line']} !important; }}
    .side-foot {{ border-top-color: {t['line']} !important; }}
    .prow {{ border-bottom-color: {t['line']} !important; }}
    .st-key-topbar {{ background: var(--bar-bg) !important;
        border-bottom-color: {t['line']} !important; }}
    [data-testid="stSidebar"] {{ background: {t['side']} !important;
        border-right: 0 !important;
        box-shadow: 2px 0 14px rgba(16, 24, 40, 0.06) !important; }}
    {_dark_extra() if theme == "dark" else ""}
    </style>
    """


def apply_theme() -> None:
    """按 session_state 注入主题样式（默认浅色）。"""
    theme = st.session_state.get("ui_theme", "light")
    bg = st.session_state.get("ui_bg", "none")
    if bg == "photo" and BG_SRC.exists() and not BG_OUT.exists():
        blur_background(int(st.session_state.get("ui_blur", DEFAULT_PREFS["blur"])))
    md(_theme_css(theme, bg))


# ---------------------------------------------------------------- 顶栏
def _chip(label: str, value: str, kind: str = "") -> str:
    cls = f"tb-chip {kind}".strip()
    return f'<span class="{cls}"><b>{views.esc(value)}</b>{views.esc(label)}</span>'


LLM_MODES = {"rules": "规则", "llm": "大模型"}


def _on_llm_change() -> None:
    """分段控件允许取消选择，这里强制回落到规则模式。"""
    if not st.session_state.get("ui_llm"):
        st.session_state["ui_llm"] = "rules"
    save_prefs()


def _mode_switch() -> None:
    """推理模式开关：开=大模型，关=规则；标签直接显示当前状态。"""
    cur = st.session_state.get("ui_mode", "rules")
    st.session_state.setdefault("tb_mode_open", cur == "llm")
    st.toggle(
        "大模型" if cur == "llm" else "规则",
        key="tb_mode_open",
        on_change=_on_mode_toggle,
        help="开：接 Qwen2.5-7B（4bit，首次约 40 秒）；关：纯规则路径，1–2 秒出结果",
    )


def _on_mode_toggle() -> None:
    """开关变化后同步到 ui_mode 并落盘。"""
    st.session_state["ui_mode"] = "llm" if st.session_state.get("tb_mode_open") else "rules"
    if st.session_state["ui_mode"] == "rules":
        # 切回规则模式就把模型卸掉，把显存还给系统
        try:
            from src.app import live

            freed = live.release_llm()
            if freed:
                st.toast(f"已释放显存 {freed} GB")
        except Exception:  # noqa: BLE001
            pass
    save_prefs()


def _on_tier_change() -> None:
    """档位切换：pills 允许反选，反选时回落到 max。"""
    if not st.session_state.get("tb_llm_tier"):
        st.session_state["tb_llm_tier"] = "max"
    st.session_state["ui_tier"] = st.session_state["tb_llm_tier"]
    save_prefs()


def _tier_switch() -> None:
    """大模型档位（eco / max）：只在开启大模型时出现。"""
    cur = st.session_state.get("ui_tier", DEFAULT_PREFS["llm_tier"])
    st.session_state.setdefault("tb_llm_tier", cur)
    st.pills(
        "档位",
        ["eco", "max"],
        key="tb_llm_tier",
        label_visibility="collapsed",
        on_change=_on_tier_change,
        help="eco：只有 Analyst 用大模型，约省一半时间；max：三层都用，质量最高但慢",
    )


def topbar() -> None:
    """通栏固定顶栏：左品牌、右两个小按钮（外观 / 刷新）。

    "数据截止 / 建议买入 / 观察" 三个徽标不放在顶栏，改由各页面自己展示
    （见 :func:`chips`）。
    """
    with st.container(key="topbar"):
        c_brand, c_act = st.columns([0.60, 0.40], vertical_alignment="center")
        with c_brand:
            md(
                f"""
                <div class="tb-brand">
                  {_LOGO_SVG}
                  <div class="tb-brand-text">
                    <div class="tb-title">A股舆情与量化策略看板</div>
                    <div class="tb-sub">千问大模型多智能体 · 证据可溯源</div>
                  </div>
                </div>
                """
            )
        with c_act:
            # 两个按钮并排贴右，间隔由 .st-key-tb_actions 控制（5px）
            with st.container(key="tb_actions", horizontal=True,
                              horizontal_alignment="right"):
                _mode_switch()
                if st.session_state.get("ui_mode") == "llm":
                    _tier_switch()
                if st.button("🎨 外观", key="tb_look", help="主题 / 背景 / 模糊度"):
                    st.session_state["_show_look"] = True
                if st.button("刷新", key="tb_refresh",
                             help="清缓存并重读数据"):
                    data.clear_caches()
                    st.rerun()
    if st.session_state.pop("_show_look", False):
        _appearance_dialog()


def chips(as_of: str, buy: int, watch: int) -> str:
    """页面内用的状态徽标（数据截止 / 建议买入 / 观察）。"""
    return (
        '<div class="tb-right">'
        f'{_chip(" 数据截止 ", as_of)}'
        f'{_chip(" 建议买入 ", str(buy), "good")}'
        f'{_chip(" 观察 ", str(watch), "warn")}'
        "</div>"
    )


@st.dialog("外观设置", width="small", dismissible=True)
def _appearance_dialog() -> None:
    """外观设置弹窗：右上角 × 或点遮罩即可关闭（Streamlit 原生 dialog）。"""
    _appearance()


def _appearance() -> None:
    """外观设置：主题 / 背景 / 模糊度，改完立即存盘，下次打开沿用。"""
    st.markdown('<div class="pop-label">主题</div>', unsafe_allow_html=True)
    st.radio(
        "主题", list(THEMES),
        format_func=lambda k: {"light": "浅色", "dark": "深色", "beige": "米色"}[k],
        key="ui_theme", horizontal=True, label_visibility="collapsed",
        on_change=save_prefs,
    )

    st.markdown('<div class="pop-label">背景</div>', unsafe_allow_html=True)
    st.radio(
        "背景", list(BACKGROUNDS),
        format_func=lambda k: {"none": "纯色", "grid": "网格", "photo": "自定义图片"}[k],
        key="ui_bg", horizontal=True, label_visibility="collapsed",
        on_change=save_prefs,
    )

    if st.session_state.get("ui_bg") != "photo":
        return

    st.slider(
        "图片模糊度", 0, 24, key="ui_blur", step=1,
        on_change=_on_blur_change,
        help="数值越大越模糊，背景更安静；文字始终清晰",
    )
    if BG_OUT.exists():
        st.image(str(BG_OUT), caption="当前背景（已按上面的模糊度处理）",
                 use_container_width=True)

    up = st.file_uploader(
        "上传背景图（JPG / PNG / WebP）", type=["jpg", "jpeg", "png", "webp"],
        key="ui_upload",
    )
    if up is not None:
        STATIC_DIR.mkdir(parents=True, exist_ok=True)
        BG_SRC.write_bytes(up.getvalue())
        blur_background(int(st.session_state.get("ui_blur", DEFAULT_PREFS["blur"])))
        save_prefs()
        st.success("背景已保存，下次打开也会保留")
        st.rerun()

    if BG_OUT.exists() and st.button("删除背景图", use_container_width=True):
        BG_SRC.unlink(missing_ok=True)
        BG_OUT.unlink(missing_ok=True)
        st.session_state["ui_bg"] = "none"
        save_prefs()
        st.rerun()


def _on_blur_change() -> None:
    blur_background(int(st.session_state.get("ui_blur", 6)))
    save_prefs()


# ---------------------------------------------------------------- 侧栏 / 页脚
def sidebar_foot() -> None:
    """侧边栏底部（CSS 固定在侧栏最下方）。"""
    with st.sidebar:
        st.markdown(
            '<div class="side-foot">'
            '<div class="side-foot-line">本地只读看板 · DuckDB</div>'
            '<div class="side-foot-line">关网页后端自动退出</div>'
            '<div class="side-foot-line">仅用于学术研究 · 不构成投资建议</div>'
            "</div>",
            unsafe_allow_html=True,
        )


def footer(meta: dict[str, Any] | None = None) -> None:
    batch = (meta or {}).get("generated_at", "")
    extra = f" · 批次 {views.esc(batch)}" if batch else ""
    md(
        f"""
        <p style="text-align:center;color:#86868b;font-size:0.75rem;margin-top:2.5rem">
        基于千问大模型多智能体的A股舆情分析与量化策略系统{extra} · 仅用于学术研究
        </p>
        """
    )


def page_head(title: str, sub: str = "") -> None:
    md(views.section(title, sub))


def busy_guard() -> bool:
    """数据更新期间屏蔽其它页面直接读库的逻辑，避免顶掉写锁。"""
    if not st.session_state.get("db_busy"):
        return False
    st.info("数据正在更新中，本页暂时不可用；请等更新结束后再查看。")
    return True











