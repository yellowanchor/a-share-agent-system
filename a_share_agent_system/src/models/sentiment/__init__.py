"""情感分析模型模块

Phase 2 实现:
    - lexicon_labeler: 金融情感词典弱监督打标（训练数据构建）
    - LoRA SFT 微调 Qwen2.5-7B-Instruct
    - 情感分类评估脚本
    - wandb 训练监控
"""

from .lexicon_labeler import LexiconLabeler

__all__ = ["LexiconLabeler"]
