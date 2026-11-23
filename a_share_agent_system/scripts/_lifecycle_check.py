# -*- coding: utf-8 -*-
"""_lifecycle_check.py — 验证"关掉网页 → 后端自动退出"。

用法：
    python scripts/_lifecycle_check.py           # 自动找 8501 的 PID
    python scripts/_lifecycle_check.py --pid 1234

流程：开无头浏览器 → 停留 6 秒让心跳跑起来 → 关闭浏览器 → 每秒检查进程是否还在。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time

EXE = os.getenv("PLAYWRIGHT_CHROME")  # 不设则用 Playwright 自带 chromium
URL = "http://localhost:8501"


def pid_on_port(port: int = 8501) -> int | None:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"(Get-NetTCPConnection -LocalPort {port} -State Listen "
         "-ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess"],
        capture_output=True, text=True,
    )
    txt = out.stdout.strip()
    return int(txt) if txt.isdigit() else None


def alive(pid: int) -> bool:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"if (Get-Process -Id {pid} -ErrorAction SilentlyContinue) {{ 'yes' }} else {{ 'no' }}"],
        capture_output=True, text=True,
    )
    return out.stdout.strip() == "yes"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, default=0)
    ap.add_argument("--port", type=int, default=8501)
    ap.add_argument("--url", default="")
    args = ap.parse_args()

    pid = args.pid or pid_on_port(args.port)
    url = args.url or f"http://localhost:{args.port}"
    if not pid:
        print(f"没找到监听 {args.port} 的进程，先启动看板。")
        return 2
    if not alive(pid):
        print(f"PID {pid} 不存在。")
        return 2
    print(f"目标进程 {pid}（{url}）存活，启动无头浏览器…")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=EXE)
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        page.goto(url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(6000)
        print(f"页面已加载，进程存活={alive(pid)}；现在关闭浏览器…")
        t0 = time.time()
        browser.close()

    for _ in range(30):
        if not alive(pid):
            print(f"✓ 后端已自动退出，用时 {time.time() - t0:.1f} 秒")
            return 0
        time.sleep(1)

    print("✗ 30 秒内没有退出（心跳/信标没生效）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
