# -*- coding: utf-8 -*-
"""SFT+RAG 三组对比评估（说明书 Phase 2 任务项 #4）
=====================================================

在 Golden Set (200 条人工标注) 上对比:
    1. base     —— Qwen2.5-7B-Instruct 零样本（已有结果, 复用）
    2. SFT      —— LoRA 微调后（已有结果, 复用）
    3. SFT+RAG  —— LoRA 微调 + 检索增强（本脚本新增）

RAG 增强方式（严守 Point-in-Time, 禁止未来函数）:
    - 以目标新闻标题为查询, 用 bge-m3 + LocalVectorStore 检索
    - 过滤条件: 同股票代码 + publish_time < 目标新闻发布时间
    - 排除目标新闻自身（标题完全相同者）
    - 取 top-3 历史相关新闻, 以「近期相关资讯」段落注入 user prompt

用法:
    python -m src.models.sentiment.evaluate_rag            # 完整评估
    python -m src.models.sentiment.evaluate_rag --limit 20 # 小样本试跑

输出:
    data/processed/eval_sentiment_sft_rag.md  三组对比总报告
"""
import argparse
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

from src.models.sentiment.evaluate import (
    BASE_DIR,
    CN2EN,
    LABEL_CN,
    LORA_DIR,
    SYSTEM_PROMPT,
    compute_metrics,
)

ROOT = Path(__file__).resolve().parents[3]
GOLDEN = ROOT / "data" / "processed" / "golden_set_200.parquet"
REPORT = ROOT / "data" / "processed"

RAG_TOP_K = 3          # 注入的相关历史新闻条数
RAG_INSTRUCTION = "\n\n近期相关资讯（供参考）：\n"

# 全量新闻词典标签（sentiment_labeled.parquet）, 供「带标签检索」变体使用
LABELED_NEWS = ROOT / "data" / "processed" / "sentiment_labeled.parquet"
LABEL_TAG = {"positive": "利好", "negative": "利空", "neutral": "中性"}


def build_rag_user_content(title: str, text: str, retrieved: list) -> str:
    """构造 RAG 增强的 user 输入（保持训练时的「标题/内容」骨架）。"""
    body = (text or "").strip()
    content = f"标题：{title}\n内容：{body}" if body and body != title else f"标题：{title}"
    if retrieved:
        refs = "\n".join(
            f"{i+1}. [{r['publish_time'][:10]}] {r['title']}"
            for i, r in enumerate(retrieved)
        )
        content += RAG_INSTRUCTION + refs
    return content


class RagAugmentedPredictor:
    """SFT 模型 + RAG 检索的联合预测器（两个模型共存于一张 16GB 显卡）."""

    def __init__(self, label_refs: bool = False) -> None:
        self.label_refs = label_refs
        # 带标签变体: 标题 → 词典情感标签映射（模拟生产环境的带标签检索）
        self._title2label = {}
        if label_refs:
            lab_df = pd.read_parquet(LABELED_NEWS, columns=["title", "label"])
            self._title2label = dict(zip(lab_df["title"], lab_df["label"]))
            print(f"带标签检索模式: 载入 {len(self._title2label)} 条新闻情感标签")

        # ---- RAG 检索器 (bge-m3, ~2.3GB 显存) ----
        from src.models.rag.retriever import NewsRetriever
        print("加载 RAG 检索器 (bge-m3 + LocalVectorStore)...")
        self.retriever = NewsRetriever()

        # ---- SFT 模型 (4bit NF4, ~4.3GB 显存) ----
        print("加载 SFT 模型 (Qwen2.5-7B 4bit + LoRA ckpt-4128)...")
        self.tokenizer = AutoTokenizer.from_pretrained(str(BASE_DIR))
        self.tokenizer.padding_side = "left"
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        bnb_cfg = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )
        base = AutoModelForCausalLM.from_pretrained(
            str(BASE_DIR), quantization_config=bnb_cfg,
            attn_implementation="sdpa", device_map="cuda:0")
        self.model = PeftModel.from_pretrained(base, str(LORA_DIR))
        self.model.eval()

    def retrieve_for(self, row) -> list:
        """按 PIT 约束检索目标新闻的同股票历史相关新闻."""
        code = str(row.get("stock_code") or "").split(",")[0].strip()
        pub = str(row.get("publish_time") or "")
        try:
            results = self.retriever.search(
                str(row["title"]),
                stock_code=code if code and code != "nan" else None,
                end=pub if pub and pub != "nan" else None,
                top_k=RAG_TOP_K + 2,  # 多取 2 条防自命中
            )
        except Exception as e:  # 检索失败降级为无 RAG
            print(f"  [warn] 检索失败({e}), 本条按无 RAG 处理")
            return []
        # 排除自身（标题相同）
        hits = [r for r in results if r["title"].strip() != str(row["title"]).strip()]
        hits = hits[:RAG_TOP_K]
        # 带标签变体: 给每条检索结果附上情感标签
        if self.label_refs:
            for r in hits:
                lab = self._title2label.get(r["title"].strip())
                if lab:
                    r["title"] = f"（{LABEL_TAG[lab]}）{r['title']}"
        return hits

    @torch.no_grad()
    def predict(self, rows, batch_size: int = 8) -> tuple:
        """逐条检索 + 批量生成. 返回 (preds, n_with_rag)."""
        # 1. 先逐条检索（编码在主存, 检索仅 3ms, 串行足够快）
        t0 = time.time()
        all_refs = []
        for _, row in rows.iterrows():
            all_refs.append(self.retrieve_for(row))
        n_with_rag = sum(1 for r in all_refs if r)
        print(f"检索完成: {len(rows)} 条, {n_with_rag} 条有历史相关新闻, "
              f"耗时 {time.time()-t0:.0f}s")

        # 2. 批量推理
        preds = []
        for i in range(0, len(rows), batch_size):
            chunk = rows[i:i + batch_size]
            refs_chunk = all_refs[i:i + batch_size]
            prompts = [
                self.tokenizer.apply_chat_template(
                    [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": build_rag_user_content(
                         r["title"], r["text"], refs)}],
                    tokenize=False, add_generation_prompt=True)
                for (_, r), refs in zip(chunk.iterrows(), refs_chunk)
            ]
            inputs = self.tokenizer(
                prompts, return_tensors="pt", padding=True,
                padding_side="left", add_special_tokens=False,
            ).to(self.model.device)
            out = self.model.generate(
                **inputs, max_new_tokens=4, do_sample=False,
                pad_token_id=self.tokenizer.pad_token_id)
            for j in range(len(chunk)):
                text = self.tokenizer.decode(
                    out[j][inputs["input_ids"].shape[1]:],
                    skip_special_tokens=True).strip()
                preds.append(next(
                    (CN2EN[w] for w in LABEL_CN.values() if w in text), "neutral"))
            print(f"  推理进度 {min(i+batch_size, len(rows))}/{len(rows)}", end="\r")
        print()
        return preds, n_with_rag


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只评前 N 条(试跑用)")
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--label-refs", action="store_true",
                    help="带标签检索变体: 检索结果附词典情感标签(模拟生产)")
    args = ap.parse_args()

    golden = pd.read_parquet(GOLDEN)
    if args.limit:
        golden = golden.head(args.limit)
    print(f"Golden Set: {len(golden)} 条")

    predictor = RagAugmentedPredictor(label_refs=args.label_refs)
    t0 = time.time()
    preds, n_with_rag = predictor.predict(golden, batch_size=args.batch_size)
    elapsed = time.time() - t0

    y_true = golden["human_label"].tolist()
    metrics = compute_metrics(y_true, preds)
    metrics["n"] = len(golden)
    metrics["n_with_rag"] = n_with_rag
    metrics["latency_avg_ms"] = elapsed / len(golden) * 1000
    print(f"SFT+RAG: Acc={metrics['accuracy']:.3f} "
          f"MacroF1={metrics['macro_f1']:.3f} "
          f"(RAG命中 {n_with_rag}/{len(golden)}, {elapsed:.0f}s)")

    # ---- 汇总三组对比报告 ----
    lex_agree = (golden["lexicon_label_v3"] == golden["human_label"]).mean() \
        if "lexicon_label_v3" in golden.columns else None
    lines = [
        "# 三组对比评估报告（Golden Set 200 条）", "",
        "| 模型 | Accuracy | Macro-F1 | 备注 |",
        "|------|----------|----------|------|",
        "| base（零样本） | 0.795 | 0.750 | 见 eval_sentiment_base_sft.md |",
        "| 词典（弱监督教师） | "
        + (f"{lex_agree:.3f}" if lex_agree is not None else "0.905")
        + " | — | lexicon_labeler v3 |",
        "| SFT（LoRA 3ep） | 0.890 | 0.858 | checkpoint-4128 |",
        f"| **SFT+RAG** | **{metrics['accuracy']:.3f}** | "
        f"**{metrics['macro_f1']:.3f}** | top-{RAG_TOP_K} PIT检索, "
        f"命中 {n_with_rag}/{len(golden)} 条 |",
        "",
        "## SFT+RAG 分类别指标", "",
        "| 类别 | Precision | Recall | F1 | 支持数 |",
        "|------|-----------|--------|----|--------|",
    ]
    for lab, r in metrics["per_class"].items():
        lines.append(f"| {LABEL_CN[lab]} | {r['precision']:.3f} "
                     f"| {r['recall']:.3f} | {r['f1']:.3f} | {r['support']} |")
    lines += ["", "## SFT+RAG 混淆矩阵（行=人工, 列=预测）", "",
              "| | 利好 | 利空 | 中性 |", "|---|---|---|---|"]
    for t in ["positive", "negative", "neutral"]:
        row = metrics["confusion"][t]
        lines.append(f"| {LABEL_CN[t]} | {row['positive']} "
                     f"| {row['negative']} | {row['neutral']} |")
    # 记录每条预测明细, 便于误差分析
    lines += ["", "## 逐条明细", "",
              "| # | 人工 | SFT+RAG | 一致 | 标题 |",
              "|---|------|---------|------|------|"]
    for i, (t, p) in enumerate(zip(y_true, preds)):
        title = str(golden.iloc[i]["title"]).replace("|", "｜")[:40]
        lines.append(f"| {i+1} | {LABEL_CN[t]} | {LABEL_CN[p]} "
                     f"| {'✓' if t == p else '✗'} | {title} |")

    variant = "labeled" if args.label_refs else "plain"
    path = REPORT / f"eval_sentiment_sft_rag_{variant}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告已保存: {path}")


if __name__ == "__main__":
    main()
