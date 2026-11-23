# -*- coding: utf-8 -*-
"""情感分类模型评估: 在 Golden Set (200 条人工标注) 上对比 base / SFT 模型.

用法:
    python -m src.models.sentiment.evaluate --model base          # 基座零样本
    python -m src.models.sentiment.evaluate --model sft          # LoRA 微调后
    python -m src.models.sentiment.evaluate --model all          # 两者对比

输出: data/processed/eval_sentiment_<model>.md + 控制台指标
指标: Accuracy / Macro-F1 / 分类别 P-R-F1 / 混淆矩阵
"""
import argparse
import json
import time
from pathlib import Path

import pandas as pd
import torch
from peft import PeftModel
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
)

ROOT = Path(__file__).resolve().parents[3]  # src/models/sentiment/ → 项目根
BASE_DIR = ROOT / "models" / "Qwen2.5-7B-Instruct"
# 3 epoch 训练完成(4137 步), trainer 记录的最优 checkpoint (eval_loss 最低)
LORA_DIR = ROOT / "models" / "sentiment_lora" / "checkpoint-4128"
GOLDEN = ROOT / "data" / "processed" / "golden_set_200.parquet"
REPORT = ROOT / "data" / "processed"

LABEL_CN = {"positive": "利好", "negative": "利空", "neutral": "中性"}
CN2EN = {v: k for k, v in LABEL_CN.items()}
SYSTEM_PROMPT = (
    "你是一名专业的A股市场舆情分析师。根据给定的新闻标题和内容，"
    "判断该新闻对相关上市公司或板块的股价影响方向。"
    "只需输出一个词：利好、利空 或 中性。不要输出任何其他内容。"
)


def build_user_content(title: str, text: str) -> str:
    """与训练集完全一致的输入构造."""
    body = (text or "").strip()
    if body and body != title:
        return f"标题：{title}\n内容：{body}"
    return f"标题：{title}"


@torch.no_grad()
def predict_batch(model, tokenizer, rows, batch_size=16, max_new_tokens=4):
    """批量生成预测, 返回解析后的英文标签列表."""
    preds = []
    for i in range(0, len(rows), batch_size):
        chunk = rows[i:i + batch_size]
        prompts = [
            tokenizer.apply_chat_template(
                [{"role": "system", "content": SYSTEM_PROMPT},
                 {"role": "user", "content": build_user_content(r["title"], r["text"])}],
                tokenize=False, add_generation_prompt=True)
            for _, r in chunk.iterrows()
        ]
        inputs = tokenizer(prompts, return_tensors="pt", padding=True,
                           padding_side="left", add_special_tokens=False).to(model.device)
        out = model.generate(**inputs, max_new_tokens=max_new_tokens,
                             do_sample=False, pad_token_id=tokenizer.pad_token_id)
        for j in range(len(chunk)):
            text = tokenizer.decode(out[j][inputs["input_ids"].shape[1]:],
                                    skip_special_tokens=True).strip()
            # 解析: 取第一个出现的合法标签词
            pred = next((CN2EN[w] for w in LABEL_CN.values() if w in text), "neutral")
            preds.append(pred)
        print(f"  推理进度 {min(i+batch_size, len(rows))}/{len(rows)}", end="\r")
    print()
    return preds


def compute_metrics(y_true: list, y_pred: list) -> dict:
    """Accuracy / Macro-F1 / 分类别指标 / 混淆矩阵."""
    labels = ["positive", "negative", "neutral"]
    acc = sum(t == p for t, p in zip(y_true, y_pred)) / len(y_true)
    report = {}
    f1s = []
    for lab in labels:
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == lab and p == lab)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != lab and p == lab)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == lab and p != lab)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        f1s.append(f1)
        report[lab] = {"precision": prec, "recall": rec, "f1": f1,
                       "support": tp + fn}
    cm = {t: {p: sum(1 for a, b in zip(y_true, y_pred) if a == t and b == p)
              for p in labels} for t in labels}
    return {"accuracy": acc, "macro_f1": sum(f1s) / len(f1s),
            "per_class": report, "confusion": cm}


def run_eval(model_name: str) -> dict:
    """加载指定模型并在 Golden Set 上评估."""
    tokenizer = AutoTokenizer.from_pretrained(str(BASE_DIR))
    tokenizer.padding_side = "left"
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    print(f"加载模型: {model_name}")
    # 与训练一致的 NF4 量化加载(bf16 15.24GB 超出本机 16GB 显存可用量)
    bnb_cfg = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        str(BASE_DIR), quantization_config=bnb_cfg,
        attn_implementation="sdpa", device_map="cuda:0")
    if model_name == "sft":
        # 4bit 基座不建议 merge(会反量化), 直接以 PeftModel 推理
        model = PeftModel.from_pretrained(model, str(LORA_DIR))
    model.eval()

    golden = pd.read_parquet(GOLDEN)
    t0 = time.time()
    preds = predict_batch(model, tokenizer, golden)
    elapsed = time.time() - t0
    metrics = compute_metrics(golden["human_label"].tolist(), preds)
    metrics["n"] = len(golden)
    metrics["latency_avg_ms"] = elapsed / len(golden) * 1000
    metrics["preds"] = preds
    print(f"  {model_name}: Acc={metrics['accuracy']:.3f} "
          f"MacroF1={metrics['macro_f1']:.3f} ({elapsed:.0f}s)")
    return metrics


def write_report(results: dict) -> Path:
    """输出 Markdown 评估报告."""
    lines = [
        "# 情感分类模型评估报告（Golden Set 200 条）", "",
        "| 模型 | Accuracy | Macro-F1 | 平均时延 |",
        "|------|----------|----------|----------|",
    ]
    for name, m in results.items():
        lines.append(f"| {name} | {m['accuracy']:.3f} | {m['macro_f1']:.3f} "
                     f"| {m['latency_avg_ms']:.0f}ms |")
    lines += ["", "## 分类别指标", "",
              "| 模型 | 类别 | Precision | Recall | F1 | 支持数 |",
              "|------|------|-----------|--------|----|--------|"]
    for name, m in results.items():
        for lab, r in m["per_class"].items():
            lines.append(f"| {name} | {LABEL_CN[lab]} | {r['precision']:.3f} "
                         f"| {r['recall']:.3f} | {r['f1']:.3f} | {r['support']} |")
    for name, m in results.items():
        lines += ["", f"## {name} 混淆矩阵（行=人工, 列=预测）", "",
                  "| | 利好 | 利空 | 中性 |", "|---|---|---|---|"]
        for t in ["positive", "negative", "neutral"]:
            row = m["confusion"][t]
            lines.append(f"| {LABEL_CN[t]} | {row['positive']} "
                         f"| {row['negative']} | {row['neutral']} |")
    suffix = "_".join(results.keys())
    path = REPORT / f"eval_sentiment_{suffix}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=["base", "sft", "all"], default="all")
    args = ap.parse_args()

    names = ["base", "sft"] if args.model == "all" else [args.model]
    results = {}
    for n in names:
        results[n] = run_eval(n)
        torch.cuda.empty_cache()

    path = write_report(results)
    print(f"\n报告已保存: {path}")


if __name__ == "__main__":
    main()
