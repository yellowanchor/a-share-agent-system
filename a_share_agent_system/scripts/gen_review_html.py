"""gen_review_html — 生成情感打标人工校验 HTML 页面
====================================================

将 200 条分层抽样样本生成单文件 HTML：
    - 每条 3 个可点击栏位（利好/利空/中性），单击选中、再点取消
    - 实时进度、与弱监督标签的一致率统计
    - 「导出 Markdown」按钮：生成与校验清单同格式的 .md 文件下载

抽样逻辑与 build_sentiment_dataset.py 完全一致（seed=42），保证样本相同。

Usage:
    cd a_share_agent_system
    python scripts/gen_review_html.py
"""

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LABELED_PARQUET = ROOT / "data" / "processed" / "sentiment_labeled.parquet"
OUT_HTML = ROOT / "data" / "processed" / "sentiment_sample_review.html"

SAMPLE_N = 200
LABEL_CN = {"positive": "利好", "negative": "利空", "neutral": "中性"}


def build_sample(df: pd.DataFrame) -> pd.DataFrame:
    """与 build_sentiment_dataset.py 相同的分层抽样（seed=42）"""
    parts = []
    for lab in ("positive", "negative", "neutral"):
        sub = df[df["label"] == lab]
        n = max(30, round(SAMPLE_N * len(sub) / len(df)))
        parts.append(sub.sample(n=min(n, len(sub)), random_state=42))
    sample = pd.concat(parts).sample(frac=1, random_state=42)
    return sample.reset_index(drop=True)


def main() -> None:
    df = pd.read_parquet(LABELED_PARQUET)
    sample = build_sample(df)

    items = []
    for _, r in sample.iterrows():
        terms = r["pos_terms"] or r["neg_terms"] or "-"
        items.append({
            "weak": r["label"],
            "weakCn": LABEL_CN[r["label"]],
            "score": round(float(r["score"]), 1),
            "title": str(r["title"]),
            "terms": str(terms),
        })

    data_json = json.dumps(items, ensure_ascii=False)

    html = HTML_TEMPLATE.replace("__DATA__", data_json)
    OUT_HTML.write_text(html, encoding="utf-8")
    print(f"生成 {OUT_HTML}（{len(items)} 条样本）")


# ---------------------------------------------------------------------------
# HTML 模板（单文件，无外部依赖，支持离线打开）
# ---------------------------------------------------------------------------
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>情感打标人工校验（200 条）</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
    background: #f5f6f8; color: #1f2329; padding: 20px;
    max-width: 980px; margin: 0 auto;
  }
  h1 { font-size: 20px; margin-bottom: 6px; }
  .sub { color: #6b7075; font-size: 13px; margin-bottom: 16px; }
  /* 顶部统计栏（吸顶） */
  .stats {
    position: sticky; top: 0; z-index: 10;
    background: #fff; border: 1px solid #e3e5e8; border-radius: 10px;
    padding: 12px 16px; margin-bottom: 14px;
    display: flex; flex-wrap: wrap; gap: 18px; align-items: center;
    box-shadow: 0 2px 8px rgba(0,0,0,.06);
  }
  .stat b { font-size: 18px; }
  .stat .ok { color: #d40000; }
  .stat .good { color: #0a8f3c; }
  .btns { margin-left: auto; display: flex; gap: 8px; }
  button {
    border: none; border-radius: 8px; padding: 8px 16px;
    font-size: 14px; cursor: pointer; font-family: inherit;
  }
  #exportBtn { background: #1652f0; color: #fff; }
  #exportBtn:hover { background: #0d41d1; }
  #copyBtn { background: #0a8f3c; color: #fff; }
  #copyBtn:hover { background: #087532; }
  #filterBtn { background: #eef0f3; color: #1f2329; }
  #filterBtn:hover { background: #e0e3e8; }
  /* 列表 */
  .item {
    background: #fff; border: 1px solid #e3e5e8; border-radius: 10px;
    padding: 12px 16px; margin-bottom: 10px;
  }
  .item.answered { border-left: 4px solid #1652f0; }
  .item .head {
    display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap;
    margin-bottom: 8px; font-size: 13px;
  }
  .idx { color: #6b7075; }
  .weak-tag {
    padding: 1px 8px; border-radius: 4px; font-size: 12px; font-weight: 600;
  }
  .weak-positive { background: #fdeaea; color: #c00; }
  .weak-negative { background: #e8f3ea; color: #0a7a35; }
  .weak-neutral  { background: #eef0f3; color: #555; }
  .score { color: #6b7075; }
  .title { font-size: 15px; line-height: 1.5; margin-bottom: 4px; }
  .terms { font-size: 12px; color: #8a9098; margin-bottom: 10px; }
  /* 复选栏位 */
  .opts { display: flex; gap: 10px; }
  .opt {
    flex: 1; text-align: center; padding: 10px 0; border-radius: 8px;
    border: 2px solid #dcdfe4; cursor: pointer; user-select: none;
    font-size: 15px; font-weight: 600; background: #fff;
    transition: all .12s;
  }
  .opt:hover { border-color: #1652f0; }
  .opt.sel-pos { background: #fdeaea; border-color: #d40000; color: #d40000; }
  .opt.sel-neg { background: #e8f3ea; border-color: #0a8f3c; color: #0a8f3c; }
  .opt.sel-neu { background: #eef0f3; border-color: #555; color: #444; }
  .opt.sel-pos::after { content: " ✓"; }
  .opt.sel-neg::after { content: " ✓"; }
  .opt.sel-neu::after { content: " ✓"; }
  .mark { font-size: 12px; color: #8a9098; margin-top: 6px; display: none; }
  .item.answered .mark { display: block; }
  .mark.agree { color: #0a8f3c; }
  .mark.disagree { color: #d40000; }
</style>
</head>
<body>
<h1>情感打标人工校验（200 条分层抽样）</h1>
<div class="sub">
  点击每条的「利好 / 利空 / 中性」栏位打钩（再点一次取消）。
  弱监督标签来自情感词典；一致率 ≥ 85% 视为通过。
</div>

<div class="stats">
  <div class="stat">已判 <b id="nDone">0</b> / 200</div>
  <div class="stat">与弱标签一致 <b id="nAgree" class="ok">0</b></div>
  <div class="stat">一致率 <b id="rate">--</b></div>
  <div class="btns">
    <button id="filterBtn" onclick="toggleFilter()">只看未判</button>
    <button id="copyBtn" onclick="copyMd()">复制 Markdown</button>
    <button id="exportBtn" onclick="exportMd()">导出 Markdown</button>
  </div>
</div>

<div id="list"></div>

<script>
const ITEMS = __DATA__;
const CN2EN = {"利好":"positive", "利空":"negative", "中性":"neutral"};
const state = new Array(ITEMS.length).fill(null);   // 每条的当前判断

const listEl = document.getElementById("list");
ITEMS.forEach((it, i) => {
  const div = document.createElement("div");
  div.className = "item";
  div.id = "item" + i;
  div.innerHTML = `
    <div class="head">
      <span class="idx">${i+1}.</span>
      <span class="weak-tag weak-${it.weak}">弱标签: ${it.weakCn}</span>
      <span class="score">score ${it.score>0?"+":""}${it.score}</span>
    </div>
    <div class="title">${it.title}</div>
    <div class="terms">命中: ${it.terms}</div>
    <div class="opts">
      <div class="opt opt-pos" onclick="pick(${i},'positive',this)">利好</div>
      <div class="opt opt-neg" onclick="pick(${i},'negative',this)">利空</div>
      <div class="opt opt-neu"  onclick="pick(${i},'neutral',this)">中性</div>
    </div>
    <div class="mark"></div>`;
  listEl.appendChild(div);
});

function pick(i, lab, el) {
  const item = document.getElementById("item" + i);
  // 再点同栏位 = 取消
  if (state[i] === lab) {
    state[i] = null;
    item.classList.remove("answered");
    el.classList.remove("sel-pos","sel-neg","sel-neu");
  } else {
    state[i] = lab;
    item.classList.add("answered");
    item.querySelectorAll(".opt").forEach(o =>
      o.classList.remove("sel-pos","sel-neg","sel-neu"));
    el.classList.add("sel-" + (lab==="positive"?"pos":lab==="negative"?"neg":"neu"));
  }
  updateMark(i);
  updateStats();
  saveState();
}

function updateMark(i) {
  const item = document.getElementById("item" + i);
  const mark = item.querySelector(".mark");
  if (!state[i]) { mark.textContent = ""; return; }
  const agree = state[i] === ITEMS[i].weak;
  mark.textContent = agree ? "✓ 与弱标签一致" : "✗ 与弱标签不一致";
  mark.className = "mark " + (agree ? "agree" : "disagree");
}

function updateStats() {
  const done = state.filter(Boolean).length;
  const agree = state.filter((s,i) => s && s === ITEMS[i].weak).length;
  document.getElementById("nDone").textContent = done;
  document.getElementById("nAgree").textContent = agree;
  document.getElementById("rate").textContent =
    done ? (agree/done*100).toFixed(1) + "%" : "--";
}

let showOnlyUnanswered = false;
function toggleFilter() {
  showOnlyUnanswered = !showOnlyUnanswered;
  document.getElementById("filterBtn").textContent =
    showOnlyUnanswered ? "显示全部" : "只看未判";
  ITEMS.forEach((_, i) => {
    const item = document.getElementById("item" + i);
    item.style.display =
      (showOnlyUnanswered && state[i]) ? "none" : "";
  });
}

function exportMd() {
  const md = buildMd();
  if (!md) { alert("还没有任何判断，请先打钩。"); return; }
  const blob = new Blob([md], {type: "text/markdown;charset=utf-8"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "sentiment_sample_review_结果.md";
  a.click();
  URL.revokeObjectURL(a.href);
}

/* 复制到剪贴板（内置预览面板拦截下载时的备选方案） */
async function copyMd() {
  const md = buildMd();
  if (!md) { alert("还没有任何判断，请先打钩。"); return; }
  try {
    await navigator.clipboard.writeText(md);
    alert("已复制到剪贴板！可直接粘贴到聊天窗口或任意文件。");
  } catch (e) {
    // 剪贴板 API 不可用时的兜底：弹出文本框手动复制
    const ta = document.createElement("textarea");
    ta.value = md; ta.style.width = "100%"; ta.style.height = "300px";
    document.body.appendChild(ta); ta.select();
    if (document.execCommand("copy")) {
      alert("已复制到剪贴板！");
    } else {
      alert("请在下方文本框中手动全选复制。");
    }
  }
}

/* 构造结果 Markdown（导出与复制共用） */
function buildMd() {
  const done = state.filter(Boolean).length;
  if (done === 0) return null;
  let agree = 0, judged = 0;
  const CN = {"positive":"利好","negative":"利空","neutral":"中性"};
  const lines = [
    "# 情感打标人工校验结果",
    "",
    `- 判断数: ${done} / 200`,
    `- 一致数: ${state.filter((s,i)=>s&&s===ITEMS[i].weak).length}`,
    `- 一致率: ${(state.filter((s,i)=>s&&s===ITEMS[i].weak).length/done*100).toFixed(1)}%`,
    "",
    "| # | 弱标签 | score | 你的判断 | 一致 | 标题 | 命中词 |",
    "|---|--------|-------|----------|------|------|--------|",
  ];
  ITEMS.forEach((it, i) => {
    const mine = state[i];
    if (!mine) return;
    judged++;
    const ok = mine === it.weak;
    if (ok) agree++;
    lines.push(`| ${i+1} | ${it.weakCn} | ${it.score} | ${CN[mine]} | ${ok?"✓":"✗"} | ${it.title} | ${it.terms} |`);
  });
  lines.push("");
  lines.push(`> 统计: 已判 ${judged} 条，一致 ${agree} 条，一致率 ${(agree/judged*100).toFixed(1)}%（≥85% 通过）`);
  return lines.join("\n");
}

/* 进度自动保存（localStorage），刷新/关闭页面不丢失 */
function saveState() {
  try { localStorage.setItem("sentiment_review_v1", JSON.stringify(state)); } catch(e) {}
}
function restoreState() {
  try {
    const saved = JSON.parse(localStorage.getItem("sentiment_review_v1") || "null");
    if (!Array.isArray(saved) || saved.length !== ITEMS.length) return;
    saved.forEach((lab, i) => {
      if (!lab) return;
      state[i] = lab;
      const item = document.getElementById("item" + i);
      item.classList.add("answered");
      const cls = lab==="positive" ? "opt-pos" : lab==="negative" ? "opt-neg" : "opt-neu";
      const el = item.querySelector("." + cls);
      el.classList.add("sel-" + (lab==="positive"?"pos":lab==="negative"?"neg":"neu"));
      updateMark(i);
    });
    updateStats();
  } catch(e) {}
}
restoreState();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
