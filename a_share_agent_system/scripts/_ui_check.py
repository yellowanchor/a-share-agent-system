# -*- coding: utf-8 -*-
"""_ui_check.py — 用 Playwright 校验「报告卡内滚动 + 展开弹窗」的实际渲染。

只用于开发期自检（不参与业务逻辑）：
    python scripts/_ui_check.py

输出：three shots 到 logs/ui/ 下 + 控制台断言结果。
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "logs" / "ui"
URL = "http://localhost:8501"

# 本机 playwright 版本与已下载的 chromium 版本不一致时，直接用系统 Chrome，
# 免去 `playwright install`（约 130MB 下载）。
CHROME_CANDIDATES = [
    Path.home() / "AppData/Local/ms-playwright/chromium-1208/chrome-win64/chrome.exe",
    Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
    Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
]


def _chrome_path() -> str | None:
    for p in CHROME_CANDIDATES:
        if p.exists():
            return str(p)
    return None


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    ok = True

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=_chrome_path())
        page = browser.new_page(viewport={"width": 1500, "height": 1000})
        page.goto(URL, wait_until="networkidle", timeout=60_000)
        page.wait_for_selector(".card-scroll", timeout=60_000)
        page.wait_for_timeout(1200)

        # 1) 三张卡片默认态：正文应被限高并可滚动
        page.locator(".card-scroll").first.scroll_into_view_if_needed()
        page.wait_for_timeout(300)
        page.screenshot(path=str(OUT / "01_cards.png"), full_page=False)

        metrics = page.evaluate(
            """() => {
            const els = [...document.querySelectorAll('.card-scroll')];
            return els.map(e => ({
                title: e.closest('.card').querySelector('.card-title').innerText.trim(),
                clientH: e.clientHeight,
                scrollH: e.scrollHeight,
                expand: !!e.closest('.card').querySelector('.card-expand'),
                modal: !!e.closest('.card-root').querySelector('.modal'),
                modalDisplay: (() => {
                    const m = e.closest('.card-root').querySelector('.modal');
                    return m ? getComputedStyle(m).display : 'none';
                })(),
            }));
        }"""
        )
        print("卡片指标：")
        for m in metrics:
            scrollable = m["scrollH"] > m["clientH"]
            print(
                f"  {m['title']}: 可视 {m['clientH']}px / 内容 {m['scrollH']}px "
                f"→ {'可滚动' if scrollable else '未超出'} | 展开按钮 {m['expand']} | 弹窗 {m['modal']}"
            )
            ok = ok and m["expand"] and m["modal"]

        # 2) 点第一张卡的「展开」→ 弹窗出现且背景模糊
        first_expand = page.locator(".card-expand").first
        first_expand.click()
        page.wait_for_timeout(500)
        shown = page.evaluate(
            """() => {
            const m = [...document.querySelectorAll('.modal')]
                .find(x => getComputedStyle(x).display !== 'none');
            if (!m) return null;
            const bd = m.querySelector('.modal-backdrop');
            const panel = m.querySelector('.modal-panel');
            return {
                display: getComputedStyle(m).display,
                blur: getComputedStyle(bd).backdropFilter,
                panelW: Math.round(panel.getBoundingClientRect().width),
                bodyScroll: m.querySelector('.modal-body').scrollHeight,
            };
        }"""
        )
        print("弹窗状态：", shown)
        ok = ok and bool(shown) and shown["display"] == "flex" and "blur" in (shown["blur"] or "")
        page.screenshot(path=str(OUT / "02_modal.png"), full_page=False)

        # 3) 点击模糊背景 → 弹窗关闭
        page.mouse.click(20, 20)
        page.wait_for_timeout(400)
        closed = page.evaluate(
            """() => [...document.querySelectorAll('.modal')]
                .every(x => getComputedStyle(x).display === 'none')"""
        )
        print("点击空白处后弹窗全部关闭：", closed)
        ok = ok and closed
        page.screenshot(path=str(OUT / "03_closed.png"), full_page=False)

        # 4) 关闭按钮也能关（再开一次）
        page.locator(".card-expand").nth(1).click()
        page.wait_for_timeout(400)
        page.locator(".modal-close:visible").first.click()
        page.wait_for_timeout(400)
        closed2 = page.evaluate(
            """() => [...document.querySelectorAll('.modal')]
                .every(x => getComputedStyle(x).display === 'none')"""
        )
        print("关闭按钮后弹窗全部关闭：", closed2)
        ok = ok and closed2

        browser.close()

    print("\n结果：", "通过" if ok else "未通过")
    print("截图：", OUT)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

