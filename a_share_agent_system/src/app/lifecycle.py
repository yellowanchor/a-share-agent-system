# -*- coding: utf-8 -*-
"""lifecycle.py — 看板进程生命周期（网页关掉 → 后端自动退出）

Streamlit 本身没有"浏览器关闭"事件，所以这里用一条极轻量的本机回路：

1. 页面里挂一个 0 高度的 iframe 组件（``components.html``），JS 在
   ``pagehide`` / ``beforeunload`` 时向 ``http://127.0.0.1:8765/bye`` 打一下；
   页面加载时打 ``/hello``，之后每 5 秒一次心跳。
2. 进程内起一个守护线程跑 ``ThreadingHTTPServer`` 接收这两个信号。
3. 守护线程每秒检查：收到过 /bye，且此后 ``grace`` 秒内没有新的 /hello，
   就 ``os._exit(0)`` 结束整个进程。

这样：
- 关标签页 / 关浏览器 → 8 秒内后端进程退出，不会留孤儿 Streamlit 占着 8501；
- 刷新页面 → pagehide 后立刻重新加载，新的 /hello 会取消退出倒计时；
- 开多个标签页 → 只要还有一个在心跳，就不会退出；
- 端口被占用（比如另开了实例）→ 静默跳过，不影响看板本身。

注意：这里用 ``keepalive`` + ``no-cors`` 发送，属于"发了就不管"的信标语义，
不依赖响应，也不会因为跨域失败而报错。
"""

from __future__ import annotations

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import streamlit as st
import streamlit.components.v1 as components

DEFAULT_PORT = 8765
DEFAULT_GRACE = 8.0

_state = {
    "started": False,
    "hello": 0.0,
    "bye": 0.0,
    "beats": 0,
}

_BEACON_JS = """
<script>
(function () {
  var base = "http://127.0.0.1:__PORT__";
  function ping(path) {
    try { fetch(base + path, { mode: "no-cors", keepalive: true }); } catch (e) {}
  }
  ping("/hello");
  setInterval(function () { ping("/hello"); }, 5000);

  var target = window;
  try { if (window.top && window.top !== window) { target = window.top; } } catch (e) {}
  try {
    target.addEventListener("pagehide", function () { ping("/bye"); });
    target.addEventListener("beforeunload", function () { ping("/bye"); });
  } catch (e) {
    window.addEventListener("pagehide", function () { ping("/bye"); });
  }
})();
</script>
"""


def _make_handler() -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            now = time.time()
            if self.path.startswith("/hello"):
                _state["hello"] = now
                _state["bye"] = 0.0
                _state["beats"] += 1
            elif self.path.startswith("/bye"):
                _state["bye"] = now
            elif self.path.startswith("/state"):
                body = (
                    '{"beats": %d, "hello_age": %.1f, "bye_age": %.1f, '
                    '"sessions": %d, "watcher": %s}'
                    % (
                        _state["beats"],
                        now - _state["hello"],
                        (now - _state["bye"]) if _state["bye"] else -1.0,
                        _state.get("sessions", -1),
                        "true" if _state.get("watcher") else "false",
                    )
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

        def log_message(self, *args):  # 静默：心跳不要刷日志
            return

    return Handler


def _janitor(grace: float) -> None:
    while True:
        time.sleep(1.0)
        bye = _state["bye"]
        if not bye:
            continue
        now = time.time()
        # 收到 bye 之后 grace 秒内没有新心跳 → 认定页面已关，退出进程
        if now - bye > grace and now - _state["hello"] > grace:
            os._exit(0)


def _watch_sessions(grace: float) -> None:
    """兜底：浏览器标签页关掉后，Streamlit 的 WebSocket 会话会消失。

    这里不依赖前端 JS——直接数服务端还有几个活跃会话；从"有过会话"变成
    "连续 grace 秒零会话"，就认定用户已经关掉页面，退出进程。
    刷新页面时新会话会在 1–2 秒内补上，不会误杀。
    """
    try:
        from streamlit.runtime import Runtime

        runtime = Runtime.instance()
    except Exception:
        return

    _state["watcher"] = True
    time.sleep(3.0)  # 等首个会话建立
    seen = False
    idle_since: float | None = None
    while True:
        time.sleep(2.0)
        try:
            n = len(runtime._session_mgr.list_active_sessions())
        except Exception:
            _state["watcher"] = False
            return  # 私有 API 变了就安静退出，不影响看板
        _state["sessions"] = n
        if n > 0:
            seen = True
            idle_since = None
            continue
        if not seen:
            continue
        idle_since = idle_since or time.time()
        if time.time() - idle_since > grace:
            os._exit(0)


def start(port: int = DEFAULT_PORT, grace: float = DEFAULT_GRACE) -> None:
    """启动信标服务 + 会话看护（每个进程只启动一次；失败静默）。"""
    if _state["started"]:
        return
    _state["started"] = True
    _state["hello"] = time.time()

    threading.Thread(target=_watch_sessions, args=(float(grace),), daemon=True).start()

    try:
        server = ThreadingHTTPServer(("127.0.0.1", int(port)), _make_handler())
    except OSError:
        return  # 端口被占用：可能已有别的实例在看护，直接跳过
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=_janitor, args=(float(grace),), daemon=True).start()


def page_beacon(port: int = DEFAULT_PORT) -> None:
    """把心跳脚本挂进页面（0 高度 iframe，不影响布局）。"""
    components.html(_BEACON_JS.replace("__PORT__", str(int(port))), height=0)
