"""build_sentiment_dataset — 全量弱监督情感打标
================================================

Phase 2 第一步：为 12.6 万条 A 股新闻生成弱监督情感标注数据集。

流程:
    1. 读取 data_platform/data/processed/news.parquet
    2. LexiconLabeler 打标（词典 + 句式 + 否定处理）
    3. 输出 data/processed/sentiment_labeled.parquet（原字段 + label/score/confidence/pos_terms/neg_terms）
    4. 打印分布统计（标签分布 / 置信度分位数 / 高置信样本数）
    5. 生成 200 条分层抽样人工校验清单（Markdown）

Usage:
    cd a_share_agent_system
    python scripts/build_sentiment_dataset.py
"""

import sys
import time
from pathlib import Path

import pandas as pd

# 项目根目录加入 sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.models.sentiment.lexicon_labeler import LexiconLabeler

# ---------------------------------------------------------------------------
# 路径配置
# ---------------------------------------------------------------------------
NEWS_PARQUET = Path("G:/daijincheng/毕业设计/data_platform/data/processed/news.parquet")
OUT_DIR = ROOT / "data" / "processed"
OUT_PARQUET = OUT_DIR / "sentiment_labeled.parquet"
REVIEW_MD = OUT_DIR / "sentiment_sample_review.md"

# 高置信阈值（用于后续 LoRA SFT 训练集筛选；说明书要求 3-5 万条）
HIGH_CONF = 0.5

# 人工校验抽样数（分层：positive/negative/neutral 按比例，最少 30 条/类）
SAMPLE_N = 200


def main() -> None:
    t0 = time.time()

    # ------------------------------------------------------------------
    # 1. 读取新闻
    # ------------------------------------------------------------------
    df = pd.read_parquet(NEWS_PARQUET)
    print(f"[1/4] 读取新闻 {len(df):,} 条  ({time.time()-t0:.1f}s)")

    # ------------------------------------------------------------------
    # 2. 批量打标
    # ------------------------------------------------------------------
    labeler = LexiconLabeler()
    records = df[["title", "text"]].to_dict("records")
    results = labeler.label_batch(records)

    df["label"] = [r.label for r in results]
    df["score"] = [r.score for r in results]
    df["confidence"] = [r.confidence for r in results]
    df["pos_terms"] = [";".join(r.pos_terms) for r in results]
    df["neg_terms"] = [";".join(r.neg_terms) for r in results]
    print(f"[2/4] 打标完成  ({time.time()-t0:.1f}s)")

    # ------------------------------------------------------------------
    # 3. 分布统计
    # ------------------------------------------------------------------
    dist = df["label"].value_counts()
    hit_rate = (df["pos_terms"].str.len() + df["neg_terms"].str.len() > 0).mean()
    print(f"[3/4] 标签分布:")
    for lab, n in dist.items():
        print(f"    {lab:>9}: {n:>7,}  ({n/len(df)*100:.1f}%)")
    print(f"    词典命中率（至少命中一个词/句式）: {hit_rate*100:.1f}%")
    print(f"    score 分位数: p10={df['score'].quantile(0.1):.2f} "
          f"p50={df['score'].quantile(0.5):.2f} p90={df['score'].quantile(0.9):.2f}")
    print(f"    confidence>=0.5 高置信样本: {(df['confidence']>=HIGH_CONF).sum():,} 条")
    for lab in ("positive", "negative"):
        sub = df[df["label"] == lab]
        n_hc = (sub["confidence"] >= HIGH_CONF).sum()
        print(f"      {lab} 高置信: {n_hc:,} 条")

    # ------------------------------------------------------------------
    # 4. 落盘
    # ------------------------------------------------------------------
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT_PARQUET, index=False)
    size_mb = OUT_PARQUET.stat().st_size / 1024 / 1024
    print(f"[4/4] 写出 {OUT_PARQUET}  ({size_mb:.1f} MB, 总耗时 {time.time()-t0:.1f}s)")

    # ------------------------------------------------------------------
    # 5. 分层抽样人工校验清单
    # ------------------------------------------------------------------
    sample_parts = []
    for lab in ("positive", "negative", "neutral"):
        sub = df[df["label"] == lab]
        n = max(30, round(SAMPLE_N * len(sub) / len(df)))
        sample_parts.append(sub.sample(n=min(n, len(sub)), random_state=42))
    sample = pd.concat(sample_parts).sample(frac=1, random_state=42)  # 打乱顺序

    lines = [
        "# 情感打标人工校验清单（分层抽样 200 条）",
        "",
        "> 校验方法：逐条阅读标题，判断情感方向是否与 label 一致。",
        "> 在「你的判断」列填写 positive/negative/neutral，最后统计一致率。",
        "> 预期：一致率 ≥ 85% 为通过（弱监督标准）。",
        "",
    ]
    for i, (_, row) in enumerate(sample.iterrows(), 1):
        terms = row["pos_terms"] or row["neg_terms"] or "-"
        lines.append(
            f"**{i}. [{row['label']}|score={row['score']}]** {row['title']}  \n"
            f"   命中: {terms} | 你的判断: ____"
        )
    REVIEW_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"    校验清单 → {REVIEW_MD}  ({len(sample)} 条)")


if __name__ == "__main__":
    main()
