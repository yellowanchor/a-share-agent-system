# -*- coding: utf-8 -*-
"""测试 Node 解密新浪K线加密数据"""
import requests, re, json, subprocess, sys

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": "https://finance.sina.com.cn"}
url = "https://finance.sina.com.cn/realstock/company/sh600000/hisdata_klc2/klc_kl.js"
r = requests.get(url, timeout=10, headers=H)
enc = re.search(r'"(.*)"', r.text).group(1)
print("加密串长度:", len(enc))

js = r'''const fs = require('fs');
eval(fs.readFileSync('scripts/sina_decode.js', 'utf8'));
const enc = process.argv[2];
try {
  const data = d(enc);
  console.log(JSON.stringify({ok: true, len: data.length, first: data[0], last: data[data.length-1]}));
} catch(e) {
  console.log(JSON.stringify({ok: false, err: String(e)}));
}'''
open('scripts/_decode_run.js', 'w', encoding='utf8').write(js)
res = subprocess.run(
    ["C:/Users/djc/.workbuddy/binaries/node/versions/22.22.2/node.exe", "scripts/_decode_run.js", enc],
    capture_output=True, text=True, timeout=30, cwd=r"G:\daijincheng\毕业设计\data_platform")
print("STDOUT:", res.stdout[:500])
print("STDERR:", res.stderr[:300])
