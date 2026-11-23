# -*- coding: utf-8 -*-
"""构造 Qwen2.5-7B-Instruct LoRA SFT 指令训练集.

数据来源: data/processed/sentiment_labeled.parquet (弱监督打标 v3, 与人工一致率 90.5%)
输出:     data/processed/sft_train.jsonl / sft_val.jsonl

规范 (项目说明书 §2/§5):
    - 规模 30,000-50,000 条, JSONL 格式, 仅纯文本+标签
    - 排除 Golden Set (200 条人工标注, 用于最终三组对比实验, 严禁入训练集)
    - 固定随机种子 42, 流程幂等可复现

采样策略:
    - positive/negative: confidence >= 0.5 高置信池 (人工抽检一致率最高)
    - neutral:   confidence == 0 (无词典命中, 纯客观播报)
    - 各类按整体弱标签分布比例配额, 总量 45,000
"""
import json
import random
from pathlib import Path

import pandas as pd

# ---------------- 配置 ----------------
SEED = 42
TOTAL = 45_000
VAL_RATIO = 0.02

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "processed" / "sentiment_labeled.parquet"
GOLDEN = ROOT / "data" / "processed" / "golden_set_200.parquet"
OUT_TRAIN = ROOT / "data" / "processed" / "sft_train.jsonl"
OUT_VAL = ROOT / "data" / "processed" / "sft_val.jsonl"

LABEL_CN = {"positive": "利好", "negative": "利空", "neutral": "中性"}

SYSTEM_PROMPT = (
    "你是一名专业的A股市场舆情分析师。根据给定的新闻标题和内容，"
    "判断该新闻对相关上市公司或板块的股价影响方向。"
    "只需输出一个词：利好、利空 或 中性。不要输出任何其他内容。"
)


def build_user_content(title: str, text: str) -> str:
    """构造用户输入（标题 + 正文，正文缺失时仅标题）."""
    body = (text or "").strip()
    if body and body != title:
        return f"标题：{title}\n内容：{body}"
    return f"标题：{title}"


def make_sample(title: str, text: str, label: str) -> dict:
    """构造一条 chat 格式样本 (Qwen chat template 兼容)."""
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_content(title, text)},
            {"role": "assistant", "content": LABEL_CN[label]},
        ],
        "label": label,  # 辅助字段, 训练时不使用
    }


def main() -> None:
    random.seed(SEED)

    df = pd.read_parquet(SRC)
    golden = pd.read_parquet(GOLDEN)
    golden_titles = set(golden["title"])

    # 1. 排除 Golden Set + 标题去重（同标题不同源只留一条）
    df = df[~df["title"].isin(golden_titles)]
    df = df.drop_duplicates(subset="title", keep="first")
    print(f"排除 Golden Set 并去重后: {len(df)} 条")

    # 2. 按类分池
    pool = {
        "positive": df[(df["label"] == "positive") & (df["confidence"] >= 0.5)],
        "negative": df[(df["label"] == "negative") & (df["confidence"] >= 0.5)],
        "neutral": df[(df["label"] == "neutral") & (df["confidence"] == 0.0)],
    }
    for k, v in pool.items():
        print(f"  {k:>8} 可用池: {len(v)}")

    # 3. 按整体弱标签分布比例分配配额（总量 TOTAL）
    dist = df["label"].value_counts(normalize=True)
    quota = {lab: int(round(TOTAL * dist[lab])) for lab in pool}
    # 修正舍入误差
    quota[max(quota, key=lambda k: quota[k])] += TOTAL - sum(quota.values())
    print(f"配额: {quota}")

    # 4. 各池随机采样（不足则全取）
    sampled = []
    for lab, sub in pool.items():
        n = min(quota[lab], len(sub))
        sampled.append(sub.sample(n=n, random_state=SEED))
    sft = pd.concat(sampled, ignore_index=True)

    # 5. 打乱 + 划分 train/val
    sft = sft.sample(frac=1.0, random_state=SEED).reset_index(drop=True)
    n_val = max(200, int(len(sft) * VAL_RATIO))
    val_df = sft.iloc[:n_val]
    train_df = sft.iloc[n_val:]

    # 6. 写出 JSONL
    for path, part in ((OUT_TRAIN, train_df), (OUT_VAL, val_df)):
        with open(path, "w", encoding="utf-8") as f:
            for _, r in part.iterrows():
                f.write(json.dumps(
                    make_sample(r["title"], r["text"], r["label"]),
                    ensure_ascii=False) + "\n")
        print(f"写出 {path.name}: {len(part)} 条")

    # 7. 统计
    print("\n=== 训练集分布 ===")
    print(train_df["label"].value_counts().to_string())
    print(f"\n验证集: {len(val_df)} 条")
    print(val_df["label"].value_counts().to_string())

    # 8. 抽样展示
    print("\n=== 样本示例 ===")
    ex = make_sample(train_df.iloc[0]["title"], train_df.iloc[0]["text"],
                     train_df.iloc[0]["label"])
    print(json.dumps(ex, ensure_ascii=False, indent=2)[:600])


if __name__ == "__main__":
    main()
