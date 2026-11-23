# -*- coding: utf-8 -*-
"""独立诊断: 定位手动层切分下训练第一步的显存去向."""
import json
import sys
import torch
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import LoraConfig, get_peft_model
from src.models.sentiment.train import split_model_across_devices, load_jsonl


def mem(tag):
    alloc = torch.cuda.memory_allocated() / 2**30
    peak = torch.cuda.max_memory_allocated() / 2**30
    print(f"[{tag:>28}] 已分配 {alloc:6.2f}GiB | 峰值 {peak:6.2f}GiB")


tok = AutoTokenizer.from_pretrained(str(ROOT / "models/Qwen2.5-7B-Instruct"))
model = AutoModelForCausalLM.from_pretrained(
    str(ROOT / "models/Qwen2.5-7B-Instruct"),
    torch_dtype=torch.bfloat16, attn_implementation="sdpa")
mem("加载完成(CPU)")
model = split_model_across_devices(model, gpu_weight_budget_gib=8.0)
mem("层切分完成")
model.config.use_cache = False

cfg = LoraConfig(r=64, lora_alpha=128, lora_dropout=0.05,
                 target_modules="all-linear", bias="none", task_type="CAUSAL_LM")
model = get_peft_model(model, cfg)
model.hf_device_map = {"manual_split": "cuda:0"}
mem("LoRA挂载")

# 构造一个 batch=2 的真实样本
samples = load_jsonl(ROOT / "data/processed/sft_train.jsonl")[:2]
feats = []
for s in samples:
    msgs = s["messages"]
    p = tok.apply_chat_template(msgs[:-1], tokenize=False, add_generation_prompt=True)
    f = tok.apply_chat_template(msgs, tokenize=False)
    pids = tok(p, add_special_tokens=False)["input_ids"]
    fids = tok(f, add_special_tokens=False)["input_ids"][:512]
    n = min(len(pids), len(fids))
    feats.append((fids, [-100] * n + fids[n:]))

maxlen = max(len(f[0]) for f in feats)
input_ids = torch.tensor([f[0] + [tok.pad_token_id] * (maxlen - len(f[0])) for f in feats]).cuda()
labels = torch.tensor([f[1] + [-100] * (maxlen - len(f[1])) for f in feats]).cuda()
attn = torch.tensor([[1] * len(f[0]) + [0] * (maxlen - len(f[0])) for f in feats]).cuda()
print(f"batch=2, 序列长度={maxlen}")

for use_ckpt in (False, True):
    print(f"\n========== 梯度检查点 = {use_ckpt} ==========")
    model.train()
    if use_ckpt:
        model.enable_input_require_grads()
        model.gradient_checkpointing_enable(
            gradient_checkpointing_kwargs={"use_reentrant": False})
    else:
        model.gradient_checkpointing_disable()
    torch.cuda.reset_peak_memory_stats()
    mem("训练前")
    out = model(input_ids=input_ids, attention_mask=attn, labels=labels)
    mem("前向完成")
    out.loss.backward()
    mem("反向完成")
    n_grad = sum(p.numel() for p in model.parameters()
                 if p.requires_grad and p.grad is not None and p.device.type == "cuda")
    print(f"   loss={out.loss.item():.4f}, GPU梯度参数 {n_grad/1e6:.0f}M")
    model.zero_grad(set_to_none=True)
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()

print("\n诊断完成")
