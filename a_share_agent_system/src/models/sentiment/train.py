# -*- coding: utf-8 -*-
"""Qwen2.5-7B-Instruct QLoRA SFT 情感分类微调.

训练配置依据《项目说明书》§3（RTX 5070 Ti 16G）:
    - lora_rank=64, lora_alpha=128, target_modules=all-linear
    - per_device_train_batch_size=8, grad_accum=4, num_epochs=3
    - bf16 计算, gradient_checkpointing, wandb 记录训练曲线

与说明书的工程偏差（均经实测验证并记录, 2026-09-11）:
    1. bf16 全量权重 → QLoRA NF4: bf16 权重 15.24GB + 训练开销 ≈ 18GB 超出
       16GB 物理上限(桌面应用常驻 1-3GB), 经用户确认改为 QLoRA,
       LoRA 适配器保持 bf16, rank=64 不变 (Dettmers et al., 2023).
    2. flash_attention_2 → sdpa: Windows 无 flash-attn 预编译 wheel.
    3. max_seq_length 2048 → 512: 数据集文本 p99 < 300 token.

显存/吞吐关键设计:
    - 尾部 logits 切片(logits_to_keep=16): 标签只存在于序列末尾的
      assistant 回复(≤8 token), 仅对最后 16 个位置过 lm_head,
      logits 显存从 ~1.2GB(batch8 全序列) 降至 ~78MB,
      避免逼近显存上限触发 Windows WDDM 显存换页(实测会慢 20 倍).
    - 左填充: 标签集中于每个样本末尾, 与尾部切片天然对齐;
      RoPE 相对位置编码 + attention_mask 屏蔽 padding, 数学等价.

用法:
    python -m src.models.sentiment.train --smoke          # 100 步试跑
    python -m src.models.sentiment.train                  # 全量训练
"""
import argparse
import json
import os
from pathlib import Path

import torch
from datasets import Dataset
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
    DataCollatorForSeq2Seq,
    Trainer,
    TrainingArguments,
)

ROOT = Path(__file__).resolve().parents[3]  # src/models/sentiment/ → 项目根
MODEL_DIR = ROOT / "models" / "Qwen2.5-7B-Instruct"
TRAIN_JSONL = ROOT / "data" / "processed" / "sft_train.jsonl"
VAL_JSONL = ROOT / "data" / "processed" / "sft_val.jsonl"
OUTPUT_DIR = ROOT / "models" / "sentiment_lora"

SEED = 42
MAX_SEQ_LEN = 512
LOGITS_KEEP = 16  # 尾部 logits 切片长度, 覆盖最长 assistant 回复


# ---------------------------------------------------------------- 数据处理
def load_jsonl(path: Path) -> list:
    """读取 JSONL 样本文件."""
    samples = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                samples.append(json.loads(line))
    return samples


def tokenize_samples(samples: list, tokenizer) -> Dataset:
    """指令掩码分词: 仅对 assistant 回复计算 loss (completion-only).

    prompt_ids = chat_template(system, user) + 生成提示
    full_ids   = chat_template(system, user, assistant)
    labels     = [-100] * len(prompt_ids) + full_ids[len(prompt_ids):]
    """
    feats = {"input_ids": [], "labels": [], "attention_mask": []}
    n_trunc = 0
    max_label_tail = 0
    for s in samples:
        msgs = s["messages"]
        prompt_text = tokenizer.apply_chat_template(
            msgs[:-1], tokenize=False, add_generation_prompt=True)
        full_text = tokenizer.apply_chat_template(msgs, tokenize=False)

        prompt_ids = tokenizer(prompt_text, add_special_tokens=False)["input_ids"]
        full_ids = tokenizer(full_text, add_special_tokens=False)["input_ids"]

        if len(full_ids) > MAX_SEQ_LEN:  # 超长截断（本数据集几乎不触发）
            full_ids = full_ids[:MAX_SEQ_LEN]
            n_trunc += 1
        n_prompt = min(len(prompt_ids), len(full_ids))
        labels = [-100] * n_prompt + full_ids[n_prompt:]
        max_label_tail = max(max_label_tail, len(full_ids) - n_prompt)

        feats["input_ids"].append(full_ids)
        feats["labels"].append(labels)
        feats["attention_mask"].append([1] * len(full_ids))

    if n_trunc:
        print(f"警告: {n_trunc} 条样本超过 {MAX_SEQ_LEN} token 被截断")
    if max_label_tail >= LOGITS_KEEP - 1:
        raise ValueError(
            f"最长标签尾段 {max_label_tail} ≥ LOGITS_KEEP-1={LOGITS_KEEP-1}, "
            f"尾部切片会丢标签, 请增大 LOGITS_KEEP")
    print(f"   标签尾段最长 {max_label_tail} token (LOGITS_KEEP={LOGITS_KEEP} 覆盖)")
    return Dataset.from_dict(feats)


# ---------------------------------------------------------------- 模型加载
def load_model():
    """加载 QLoRA 基座: NF4 4bit 量化 + bf16 计算精度.

    决策记录(2026-09-11): bf16 全量权重 15.24GB + 训练开销 ~2.9GB ≈ 18GB,
    超出 5070 Ti 16GB 物理上限(桌面应用常驻占用 1-3GB, 可用 ~12.9GB),
    bf16 路线实测不可行(accelerate meta-device 梯度 bug / CPU 切分单步分钟级),
    经用户确认改为 QLoRA: 基座 NF4 量化(~4.3GB), LoRA 适配器保持 bf16,
    学术标准做法(Dettmers et al., 2023), rank=64 与说明书一致.
    """
    bnb_cfg = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",            # NF4: 正态分布最优 4bit 编码
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,       # 量化常数再量化, 再省 ~0.4GB
    )
    print("加载基座模型 (QLoRA NF4 + bf16 计算, sdpa)")
    return AutoModelForCausalLM.from_pretrained(
        str(MODEL_DIR),
        quantization_config=bnb_cfg,
        attn_implementation="sdpa",  # Windows 无 flash-attn 预编译, 用原生 sdpa
        device_map={"": 0},
    )


def build_lora_model(model):
    """按说明书 §3 配置挂载 LoRA 适配器."""
    cfg = LoraConfig(
        r=64,
        lora_alpha=128,
        lora_dropout=0.05,
        target_modules="all-linear",
        bias="none",
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, cfg)
    model.print_trainable_parameters()
    return model


# ---------------------------------------------------------------- 自定义 Trainer
class SparseLogitsTrainer(Trainer):
    """尾部 logits 切片训练器.

    标签仅存在于序列末尾(左填充保证), 仅对最后 LOGITS_KEEP 个位置计算
    lm_head logits, 将 logits 显存从 O(B*L*V) 降至 O(B*K*V).
    对齐关系(位置 p 的 hidden 预测 labels[p+1]):
        logits[:, i] ↔ position L-K+i
        shift_labels[:, i] = labels[:, L-K+1+i]  (i < K-1)
        shift_labels[:, K-1] = -100              (最后位置无下一 token)
    """

    def compute_loss(self, model, inputs, return_outputs=False,
                     num_items_in_batch=None):
        labels = inputs["labels"]
        K = LOGITS_KEEP
        # [B, K-1] = labels[:, L-K+1 : L], 再补一列 -100 凑 [B, K]
        tail = labels[:, -(K - 1):]
        pad_col = torch.full((labels.size(0), 1), -100,
                             device=labels.device, dtype=labels.dtype)
        inputs = dict(inputs)
        inputs["shift_labels"] = torch.cat([tail, pad_col], dim=1)
        inputs["logits_to_keep"] = K
        inputs["num_items_in_batch"] = num_items_in_batch
        outputs = model(**inputs)
        loss = outputs.loss
        return (loss, outputs) if return_outputs else loss

    def _print_device_layout(self):
        n_cuda = sum(p.numel() for p in self.model.parameters()
                     if p.device.type == "cuda")
        n_cpu = sum(p.numel() for p in self.model.parameters()
                    if p.device.type == "cpu")
        alloc = torch.cuda.memory_allocated() / 2**30 if torch.cuda.is_available() else 0
        print(f"   设备布局: GPU {n_cuda/1e9:.2f}B 参数 / CPU {n_cpu/1e9:.2f}B 参数 "
              f"| 当前 GPU 已分配 {alloc:.2f}GiB")


# ---------------------------------------------------------------- 训练
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="100 步试跑验证显存/吞吐")
    ap.add_argument("--batch-size", type=int, default=8,
                    help="说明书默认 8, OOM 时降 4")
    ap.add_argument("--epochs", type=float, default=3.0)
    ap.add_argument("--resume", type=str, default=None,
                    help="从指定 checkpoint 目录续训(含优化器/调度器/随机状态)")
    args = ap.parse_args()

    # wandb 离线模式: 无账号也可记录完整训练曲线, 之后可 `wandb sync` 上传
    os.environ.setdefault("WANDB_MODE", "offline")
    os.environ.setdefault("WANDB_PROJECT", "a-share-sentiment-sft")
    # Windows 不支持 expandable_segments; 用 GC 阈值+分割上限抑制碎片
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF",
                          "garbage_collection_threshold:0.6,max_split_size_mb:512")

    print("=" * 60)
    print("1. 加载分词器与数据")
    tokenizer = AutoTokenizer.from_pretrained(str(MODEL_DIR))
    tokenizer.padding_side = "left"  # 标签集中于尾部, 配合尾部 logits 切片
    train_ds = tokenize_samples(load_jsonl(TRAIN_JSONL), tokenizer)
    val_ds = tokenize_samples(load_jsonl(VAL_JSONL), tokenizer)
    print(f"   训练 {len(train_ds)} 条 / 验证 {len(val_ds)} 条")

    print("2. 加载基座模型 (QLoRA NF4)")
    model = load_model()
    model.config.use_cache = False
    # 4bit 基座挂 LoRA 前的标准准备: 冻结量化权重 + 输入梯度 + 梯度检查点
    model = prepare_model_for_kbit_training(
        model, use_gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False})
    model = build_lora_model(model)

    total_steps_per_epoch = len(train_ds) // (args.batch_size * 4)
    eval_steps = max(50, total_steps_per_epoch // 4)

    targs = TrainingArguments(
        output_dir=str(OUTPUT_DIR),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=max(4, args.batch_size // 2),
        gradient_accumulation_steps=4,
        learning_rate=1e-4,
        lr_scheduler_type="cosine",
        warmup_ratio=0.03,
        weight_decay=0.01,
        bf16=True,
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        eval_strategy="steps",
        eval_steps=eval_steps,
        save_strategy="steps",
        save_steps=eval_steps,
        save_total_limit=3,
        load_best_model_at_end=True,
        metric_for_best_model="eval_loss",
        logging_steps=10,
        report_to="wandb",
        seed=SEED,
        data_seed=SEED,
        dataloader_num_workers=0,  # Windows 多进程 dataloader 易死锁
        remove_unused_columns=False,
    )
    if args.smoke:
        targs.max_steps = 100
        targs.eval_strategy = "steps"
        targs.eval_steps = 50
        targs.save_strategy = "no"
        targs.load_best_model_at_end = False
        targs.output_dir = str(OUTPUT_DIR) + "_smoke"

    print("3. 开始训练")
    trainer = SparseLogitsTrainer(
        model=model,
        args=targs,
        train_dataset=train_ds,
        eval_dataset=val_ds,
        data_collator=DataCollatorForSeq2Seq(  # 左填充, label 填 -100
            tokenizer=tokenizer, model=None, padding=True, label_pad_token_id=-100),
    )
    trainer._print_device_layout()
    result = trainer.train(resume_from_checkpoint=args.resume)
    print(f"训练完成: {result.metrics}")

    if not args.smoke:
        print("4. 保存 LoRA 权重")
        model.save_pretrained(str(OUTPUT_DIR))
        tokenizer.save_pretrained(str(OUTPUT_DIR))
        print(f"   已保存至 {OUTPUT_DIR}")

    # 显存峰值
    peak = torch.cuda.max_memory_allocated() / 2**30 if torch.cuda.is_available() else 0
    print(f"GPU 显存峰值: {peak:.2f} GB")


if __name__ == "__main__":
    main()
