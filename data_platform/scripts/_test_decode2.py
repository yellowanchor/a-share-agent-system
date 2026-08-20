# -*- coding: utf-8 -*-
"""测试 Node 常驻解密服务"""
import requests, re, json, subprocess, sys, threading, time
from pathlib import Path

BASE = Path(r"G:\daijincheng\毕业设计\data_platform")
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": "https://finance.sina.com.cn"}

# 启动常驻 node 服务
proc = subprocess.Popen(
    ["C:/Users/djc/.workbuddy/binaries/node/versions/22.22.2/node.exe", "scripts/sina_decode_server.js"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    cwd=str(BASE), text=True, encoding="utf-8", bufsize=1)
print("node 进程已启动 pid=", proc.pid)

def decode(enc, timeout=20):
    """同步解密调用"""
    req_id = id(enc) % 1000000
    proc.stdin.write(json.dumps({"id": req_id, "enc": enc}) + "\n")
    proc.stdin.flush()
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("node 服务退出")
        try:
            resp = json.loads(line)
        except Exception:
            continue
        if resp.get("id") == req_id:
            if resp.get("ok"):
                return resp["data"]
            raise RuntimeError(resp.get("err"))
    raise TimeoutError("解密超时")

# 拉取600000全量历史
url = "https://finance.sina.com.cn/realstock/company/sh600000/hisdata_klc2/klc_kl.js"
r = requests.get(url, timeout=10, headers=H)
enc = re.search(r'"(.*)"', r.text).group(1)
print("加密串长度:", len(enc))

t0 = time.time()
data = decode(enc)
print(f"解密OK: {len(data)}条 耗时{time.time()-t0:.3f}s")
print("首条:", data[0])
print("末条:", data[-1])

# 字段检查
keys = set()
for d in data[:200]:
    keys.update(d.keys())
print("字段:", sorted(keys))

# 再测一次(复用进程)
t0 = time.time()
url2 = "https://finance.sina.com.cn/realstock/company/sz000001/hisdata_klc2/klc_kl.js"
r2 = requests.get(url2, timeout=10, headers=H)
enc2 = re.search(r'"(.*)"', r2.text).group(1)
data2 = decode(enc2)
print(f"第二次解密OK: {len(data2)}条 耗时{time.time()-t0:.3f}s 首={data2[0]['d'] if 'd' in data2[0] else data2[0]}")

proc.terminate()
print("测试完成")
