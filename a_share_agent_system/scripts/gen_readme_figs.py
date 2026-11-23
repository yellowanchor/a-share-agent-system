# -*- coding: utf-8 -*-
"""生成 README 用的架构图与流程图（口径与代码一致，2026-11 现状）。

运行：
    python scripts/gen_readme_figs.py
输出：
    <repo>/assets/architecture.png  (2970x1920 @300dpi)
    <repo>/assets/flow.png          (3420x1320 @300dpi)

为什么单独写一个脚本：旧图把技术栈写成了 DeepSeek-R1-Distill / HNSW / Tushare，
与当前实现（单一 Qwen2.5-7B、numpy 精确余弦、腾讯+新浪源）不一致，必须重画。
"""
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

# ---------- 中文字体（Windows 自带微软雅黑）----------
_font = Path(r"C:\Windows\Fonts\msyh.ttc")
if _font.exists():
    font_manager.fontManager.addfont(str(_font))
    CN = font_manager.FontProperties(fname=str(_font)).get_name()
    plt.rcParams["font.sans-serif"] = [CN]
    plt.rcParams["axes.unicode_minus"] = False

ROOT = Path(__file__).resolve().parents[1]          # a_share_agent_system/
OUT = ROOT.parent / "assets"                        # 仓库根 assets/
OUT.mkdir(exist_ok=True)

# ---------- 配色（与 README 的浅色系一致）----------
BLUE = ("#EAF1FB", "#3C6FB5")
RED = ("#FBEDED", "#B5544F")
AMBER = ("#FDF3E3", "#B98A38")
GREEN = ("#EAF4EC", "#3E8455")
PURPLE = ("#F0EDF9", "#6A5AA8")
GRAY_BOX = ("#F5F5F7", "#000000")
TEXT, SUB = "#1D1D1F", "#4A4A4F"


def box(ax, x, y, w, h, face, edge, lw=1.6, r=0.05):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle=f"round,pad=0,rounding_size={r}",
        linewidth=lw, edgecolor=edge, facecolor=face, zorder=2))


def arrow(ax, x1, y1, x2, y2, color="#8A8A8E", lw=2.2):
    ax.add_patch(FancyArrowPatch(
        (x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=22,
        linewidth=lw, color=color, shrinkA=0, shrinkB=0, zorder=3))


def center(ax, x, y, s, size, color=TEXT, weight="normal"):
    ax.text(x, y, s, ha="center", va="center", fontsize=size,
            color=color, fontweight=weight, zorder=4)


# ============ 图 1：系统架构（四层，自下而上）============
def build_architecture():
    fig, ax = plt.subplots(figsize=(9.9, 6.4), dpi=300)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 6.5)
    ax.axis("off")
    fig.patch.set_facecolor("#FFFFFF")

    rows = [
        ("应用层", PURPLE, "Streamlit 五页看板",
         "每日建议 · 个股分析 · 模拟持仓 · 股市入门 · 数据更新"),
        ("智能体层", AMBER, "Analyst 研判 → Strategist 策略 → Critic 校验",
         "证据链 [证据 n] 可溯源 ｜ 13 项确定性规则 + 大模型语义 ｜ 迭代闭环反馈重试"),
        ("检索层", RED, "bge-m3 嵌入 + 自研 LocalVectorStore",
         "numpy 精确余弦 ｜ 个股与时间过滤 ｜ md5 幂等增量更新 ｜ 端到端 ≈ 111 ms"),
        ("数据层", BLUE, "腾讯行情（主） + 新浪（备） + 东方财富新闻 + 财报",
         "DuckDB：行情 1,787 万行 ｜ 新闻 12.6 万条 ｜ 财报 32 万行 ｜ 前复权 + 不复权双份"),
    ]

    top, h, gap, lbl_w = 5.55, 1.05, 0.42, 1.55
    step = h + gap
    for i, (name, (face, edge), title, sub) in enumerate(rows):
        y = top - i * step
        box(ax, 0.15, y, lbl_w, h, GRAY_BOX[0], GRAY_BOX[1], lw=1.5)
        center(ax, 0.15 + lbl_w / 2, y + h / 2, name, 15, TEXT, "bold")
        box(ax, 1.95, y, 7.9, h, face, edge)
        center(ax, 1.95 + 7.9 / 2, y + h * 0.66, title, 15.5, TEXT, "bold")
        center(ax, 1.95 + 7.9 / 2, y + h * 0.30, sub, 10.5, SUB)
        if i < len(rows) - 1:  # 架构自下而上：箭头朝上（数据层 → 应用层）
            arrow(ax, 5.9, y - gap + 0.04, 5.9, y - 0.04)

    ax.text(0.15, 0.30,
            "硬件基线：NVIDIA RTX 5070 Ti 16 GB 显存 + 64 GB 内存 ｜ 全程本地化运行，数据不出本机",
            fontsize=10, color=SUB, ha="left", va="center")

    out = OUT / "architecture.png"
    fig.savefig(out, dpi=300, facecolor="#FFFFFF", bbox_inches="tight", pad_inches=0.18)
    plt.close(fig)
    print("已生成:", out)


# ============ 图 2：工作流程（五阶段，自左向右）============
def build_flow():
    fig, ax = plt.subplots(figsize=(11.4, 4.4), dpi=300)
    ax.set_xlim(0, 11.4)
    ax.set_ylim(0, 4.4)
    ax.axis("off")
    fig.patch.set_facecolor("#FFFFFF")

    stages = [
        ("① 数据采集", BLUE, ["腾讯 / 新浪 / 东财", "增量 + 幂等去重"]),
        ("② 治理与入库", RED, ["PIT 财务对齐", "DuckDB 双份复权"]),
        ("③ 情感与检索", AMBER, ["QLoRA 微调 89.0%", "bge-m3 向量库"]),
        ("④ 多智能体研判", GREEN, ["三 Agent + 闸门", "证据链编号"]),
        ("⑤ 应用交付", PURPLE, ["五页看板", "推荐 · 模拟盘"]),
    ]

    w, h, y0, gap = 1.85, 1.75, 1.30, 0.40
    x = 0.30
    for i, (name, (face, edge), lines) in enumerate(stages):
        box(ax, x, y0, w, h, face, edge)
        center(ax, x + w / 2, y0 + h * 0.78, name, 12.5, TEXT, "bold")
        center(ax, x + w / 2, y0 + h * 0.44, lines[0], 9.5, SUB)
        center(ax, x + w / 2, y0 + h * 0.22, lines[1], 9.5, SUB)
        if i < len(stages) - 1:
            arrow(ax, x + w + 0.03, y0 + h / 2, x + w + gap - 0.03, y0 + h / 2)
        x += w + gap

    center(ax, 11.4 / 2, 0.78,
           "数据基座 → 检索增强 → 协同推理 → 策略生成 → 应用交付", 11, SUB)
    center(ax, 11.4 / 2, 0.38,
           "每个阶段都有可运行的校验脚本（validate_rag / build_sentiment_dataset / batch_eval / pytest）",
           9.5, "#8A8A8E")

    out = OUT / "flow.png"
    fig.savefig(out, dpi=300, facecolor="#FFFFFF", bbox_inches="tight", pad_inches=0.18)
    plt.close(fig)
    print("已生成:", out)


if __name__ == "__main__":
    build_architecture()
    build_flow()
    sys.exit(0)

