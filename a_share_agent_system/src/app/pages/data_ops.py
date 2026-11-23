# -*- coding: utf-8 -*-
"""数据更新页：在网页里点一下就完成"行情+新闻增量 → 重建推荐 → 刷新看板"。

为什么要在这里做：
DuckDB 是**单写者**数据库——写任务持锁期间任何只读连接都会被拒。所以点「开始更新」
时本页会先把自己这边的读取缓存清空、进入"更新中"状态（其它页面自动显示提示），
把数据库完全让给写进程；更新结束后再清缓存、重新打开读连接。

对应到脚本：
    1. scripts/update_daily.py  --all --threads 4 --pace 0.25   增量行情 + L1 新闻
    2. scripts/run_daily_signals.py --limit 20                  新闻闸门 + 四因子打分
    3. scripts/archive_cold.py --days 120 --compact --dry-run   冷数据归档（只看不删）
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterator

import streamlit as st

from src.app import data, layout, views

ROOT = Path(__file__).resolve().parents[3]
PY = str(ROOT.parent / "anaconda3" / "python.exe")
if not Path(PY).exists():  # 兜底：用当前解释器
    PY = sys.executable


def _stream(cmd: list[str], cwd: Path) -> Iterator[str]:
    """逐行跑子进程并 yield 输出（刷新页面不会中断子进程之外的东西）。"""
    env = None
    proc = subprocess.Popen(
        cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1, env=env,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        yield line.rstrip("\n")
    proc.wait()
    yield f"[exit code {proc.returncode}]"


def _run_jobs(jobs: list[tuple[str, list[str]]]) -> bool:
    ok = True
    for title, cmd in jobs:
        with st.status(f"{title}…", expanded=True) as status:
            st.caption("$ " + " ".join(cmd))
            box = st.empty()
            tail: list[str] = []
            for line in _stream(cmd, ROOT):
                if line.startswith("[exit code"):
                    if line != "[exit code 0]":
                        ok = False
                        status.update(label=f"{title} 失败", state="error")
                        st.error(line)
                    else:
                        status.update(label=f"{title} 完成", state="complete")
                    break
                tail.append(line)
                box.code("\n".join(tail[-18:]), language="text")
            else:
                status.update(label=f"{title} 结束", state="complete")
    return ok


def render() -> None:
    stats = data.db_stats()
    run = data.last_run()
    sig, sig_date = data.latest_signal()

    layout.page_head("数据更新", "行情与新闻增量入库 → 重算每日建议 → 看板立即读到新数据")

    # ---------------------------------------------------------------- 状态卡
    if stats.get("ok"):
        _ts = str(run.get("finished_at") or run.get("started_at") or "")
        _last_val = _ts[11:16] if len(_ts) >= 16 else "—"
        _last_lab = f"上次更新 {_ts[5:10]}" if len(_ts) >= 16 else "上次更新"
        layout.md(
            views.metrics_row([
                (f"{stats['rows'] / 10000:.0f}<span class='unit'>万行</span>", "行情总行数"),
                (str(stats.get("last_date", "—")), "最新交易日"),
                (f"{stats.get('news', 0) / 10000:.1f}<span class='unit'>万</span>", "新闻条数"),
                (sig_date or "—", "推荐文件日期"),
                (_last_val, _last_lab),
            ])
        )
    else:
        st.warning("读不到行情库（可能正在被写任务占用）。")

    if st.session_state.get("db_busy"):
        st.info("更新进行中，请等待完成；期间其它页面会显示占位提示。")

    # ---------------------------------------------------------------- 操作区
    c0, c1, c2, c3 = st.columns([1.1, 1, 1, 1.4], gap="medium")
    with c0:
        _t = st.pills(
            "推荐研判档位", ["none", "eco", "max"],
            default="none",
            format_func=lambda k: {"none": "规则", "eco": "eco", "max": "max"}[k],
            key="ops_tier",
            help="规则：20 只约 30 秒｜eco：仅 Analyst 走大模型，约 6–8 分钟｜max：三层都走，约 12–15 分钟",
        )
        _llm_arg = _t or "none"
    with c1:
        full = st.button("更新行情 + 新闻", type="primary", use_container_width=True,
                         disabled=bool(st.session_state.get("db_busy")))
    with c2:
        only_sig = st.button("只重算推荐", use_container_width=True,
                             disabled=bool(st.session_state.get("db_busy")))
    with c3:
        st.caption(
            "更新耗时通常 3–10 分钟（视行情源限流情况）。"
            "更新时请勿关闭页面——关掉页面会连带退出后端。"
        )

    if full:
        st.session_state["db_busy"] = True
        data.clear_caches()  # 先放掉读取缓存，把库完整让给写进程
        try:
            ok = _run_jobs([
                ("增量行情 + 新闻",
                 [PY, "-u", "scripts/update_daily.py", "--all",
                  "--threads", "4", "--pace", "0.25"]),
                ("重算每日建议",
                 [PY, "-u", "scripts/run_daily_signals.py", "--limit", "20",
                  "--llm", _llm_arg]),
            ])
        finally:
            st.session_state["db_busy"] = False
            data.clear_caches()
        if ok:
            st.success("更新完成，看板已刷新。")
            time.sleep(0.5)
            st.rerun()
        else:
            st.error("更新过程中有步骤失败，请查看上面的日志尾部。")

    if only_sig:
        st.session_state["db_busy"] = True
        try:
            ok = _run_jobs([
                ("重算每日建议",
                 [PY, "-u", "scripts/run_daily_signals.py", "--limit", "20",
                  "--llm", _llm_arg]),
            ])
        finally:
            st.session_state["db_busy"] = False
            data.clear_caches()
        if ok:
            st.rerun()

    # ---------------------------------------------------------------- 推荐预览
    if sig:
        items = sig.get("items") or []
        top = sorted(items, key=lambda x: -(x.get("score") or 0))[:8]
        layout.page_head("最近一次推荐（Top 8）", f"数据截止 {sig_date}")
        rows = "".join(
            f'<div class="prow"><span class="pr-name">{views.esc(it.get("name") or "")} '
            f'<span class="pr-code">{str(it.get("code") or "")[-6:]}</span></span>'
            f'<span class="pr-score">{float(it.get("score") or 0):.0f} 分</span>'
            f'<span class="pr-reason">{views.esc(it.get("verdict"))} · '
            f'买入 {data.fmt_num(it.get("entry"))} / 止损 {data.fmt_num(it.get("stop_loss"))}'
            f"</span></div>"
            for it in top
        )
        layout.md(rows)

    # ---------------------------------------------------------------- 定时任务
    layout.page_head("定时任务", "Windows 计划任务，无需打开看板也会自动跑")
    tasks = [
        ("17:45", "每日·行情 + 新闻增量", "ASHARE_Daily_Update", "大约 3–10 分钟"),
        ("18:50", "每日·重算推荐", "ASHARE_Daily_Signals", "约 30–60 秒"),
        ("每月 1 日 03:00", "冷数据归档", "ASHARE_Monthly_Archive", "按 120 天切片"),
    ]
    rows = "".join(
        f'<div class="task-row">'
        f'<span class="t-time">{t}</span>'
        f'<span class="t-name">{name}</span>'
        f'<span class="t-task">{code}</span>'
        f'<span class="t-meta">{hint}</span>'
        f"</div>"
        for t, name, code, hint in tasks
    )
    layout.md(f'<div class="task-card">{rows}</div>')
    st.caption(
        "注册/重建：`powershell -ExecutionPolicy Bypass -File scripts/install_tasks.ps1`；"
        "查看：`schtasks /Query /TN ASHARE_Daily_Update`；日志在 `logs/` 下。"
    )
