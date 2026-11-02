# -*- coding: utf-8 -*-
"""qwen.py — Qwen2.5-7B-Instruct 本地推理后端（4bit NF4）

显存约束（本机 RTX 5070 Ti 16GB）
--------------------------------
bf16 加载 7B 需 15.24GB，加上激活与 KV Cache 必然 OOM（Phase 2 已实测）。
故统一走 QLoRA 同款 NF4 量化：基座约 4.3GB，留给 bge-m3（约 2.3GB）
与生成缓存的空间充足，两者可同时驻留。

关于是否挂载情感 LoRA
--------------------
默认 **不挂载**。`models/sentiment_lora` 是为三分类任务微调的，其输出分布
被压缩到「利好/利空/中性」三个词上；用它做长文本报告生成会引入严重的
格式偏置（实测倾向输出极短序列）。因此：
    - 情感分类  → 用 Phase 2 的 evaluate.py / lexicon_labeler
    - 报告生成  → 用本后端的基座（use_lora=False）
如确需对比实验，可 `use_lora=True` 显式挂载。

用法:
    from src.models.llm import get_backend
    llm = get_backend("qwen")
    print(llm.chat("你是分析师", "总结一下"))
    llm.unload()          # 释放显存
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from src.models.llm.base import LLMBackend

logger = logging.getLogger(__name__)

__all__ = ["QwenBackend"]

# src/models/llm/qwen.py → 向上 3 级到 a_share_agent_system/
ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BASE_DIR = ROOT / "models" / "Qwen2.5-7B-Instruct"
DEFAULT_LORA_DIR = ROOT / "models" / "sentiment_lora" / "checkpoint-4128"


class QwenBackend(LLMBackend):
    """Qwen2.5-7B-Instruct 4bit 推理后端（懒加载 + 可释放）。

    Attributes:
        base_dir: 基座模型目录。
        lora_dir: LoRA 权重目录（use_lora=True 时生效）。
        use_lora: 是否挂载情感 LoRA（默认 False，见模块文档）。
        device: 推理设备，默认 cuda:0。
    """

    name = "qwen2.5-7b-instruct"

    def __init__(
        self,
        base_dir: Optional[Path] = None,
        lora_dir: Optional[Path] = None,
        use_lora: bool = False,
        device: str = "cuda:0",
        load_in_4bit: bool = True,
        max_new_tokens: int = 1024,
    ) -> None:
        """初始化（不立即加载模型）。

        Args:
            base_dir: 基座目录，默认 models/Qwen2.5-7B-Instruct。
            lora_dir: LoRA 目录，默认 checkpoint-4128。
            use_lora: 是否挂载 LoRA。
            device: 目标设备。
            load_in_4bit: 4bit NF4 量化（强烈建议保持 True）。
            max_new_tokens: 默认最大生成长度。
        """
        self.base_dir = Path(base_dir) if base_dir else DEFAULT_BASE_DIR
        self.lora_dir = Path(lora_dir) if lora_dir else DEFAULT_LORA_DIR
        self.use_lora = use_lora
        self.device = device
        self.load_in_4bit = load_in_4bit
        self.default_max_new_tokens = max_new_tokens
        self._model = None
        self._tokenizer = None
        logger.debug("QwenBackend 已创建（未加载）: %s", self.base_dir)

    # ------------------------------------------------------------------
    # 模型生命周期
    # ------------------------------------------------------------------
    @property
    def is_loaded(self) -> bool:
        """模型是否已载入显存。"""
        return self._model is not None

    def load(self) -> None:
        """加载模型与分词器（幂等）。"""
        if self._model is not None:
            return
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        if not self.base_dir.exists():
            raise FileNotFoundError(
                f"基座模型不存在: {self.base_dir}，请先从 ModelScope 下载"
            )

        tok = AutoTokenizer.from_pretrained(str(self.base_dir))
        if tok.pad_token is None:
            tok.pad_token = tok.eos_token

        quant_cfg = None
        if self.load_in_4bit and self.device.startswith("cuda"):
            from transformers import BitsAndBytesConfig
            quant_cfg = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_use_double_quant=True,
            )
        model = AutoModelForCausalLM.from_pretrained(
            str(self.base_dir),
            quantization_config=quant_cfg,
            attn_implementation="sdpa",
            device_map=self.device,
        )
        # 4bit 基座不做 merge（merge 会触发反量化，显存翻倍）
        if self.use_lora:
            from peft import PeftModel
            if not self.lora_dir.exists():
                raise FileNotFoundError(f"LoRA 权重不存在: {self.lora_dir}")
            model = PeftModel.from_pretrained(model, str(self.lora_dir))
            logger.info("已挂载情感 LoRA: %s", self.lora_dir)
        model.eval()

        self._model = model
        self._tokenizer = tok
        logger.info(
            "Qwen 后端就绪: base=%s lora=%s 4bit=%s",
            self.base_dir.name, self.use_lora, self.load_in_4bit,
        )

    def unload(self) -> None:
        """释放模型与显存（与 bge-m3 串行部署时用）。"""
        if self._model is None:
            return
        del self._model
        self._model = None
        self._tokenizer = None
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:  # pragma: no cover
            pass
        logger.info("Qwen 后端已卸载")

    def close(self) -> None:
        """兼容基类接口，等价于 unload。"""
        self.unload()

    # ------------------------------------------------------------------
    # 推理
    # ------------------------------------------------------------------
    def chat(
        self,
        system: str,
        user: str,
        temperature: float = 0.1,
        max_new_tokens: int = 1024,
        role: str = "",
        **kwargs: Any,
    ) -> str:
        """单轮对话生成。

        Args:
            system: 系统提示词。
            user: 用户提示词。
            temperature: 采样温度（0 为贪心解码）。
            max_new_tokens: 最大新生成 token 数。
            role: 调用方角色标识（真实推理不使用，仅为统一接口签名）。
            **kwargs: 透传 generate（如 repetition_penalty）。

        Returns:
            模型输出文本（已去除首尾空白）。
        """
        import torch

        self.load()
        assert self._tokenizer is not None and self._model is not None

        prompt = self._tokenizer.apply_chat_template(
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            tokenize=False,
            add_generation_prompt=True,
        )
        inputs = self._tokenizer(prompt, return_tensors="pt").to(self._model.device)
        gen_kwargs = dict(
            max_new_tokens=max_new_tokens or self.default_max_new_tokens,
            do_sample=temperature > 0,
            pad_token_id=self._tokenizer.pad_token_id,
        )
        if temperature > 0:
            gen_kwargs["temperature"] = temperature
        gen_kwargs.update({
            k: v for k, v in kwargs.items()
            if k in ("repetition_penalty", "top_p", "top_k", "no_repeat_ngram_size")
        })

        with torch.no_grad():
            out = self._model.generate(**inputs, **gen_kwargs)
        text = self._tokenizer.decode(
            out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True
        )
        return text.strip()

    def __repr__(self) -> str:  # pragma: no cover
        state = "loaded" if self.is_loaded else "lazy"
        return (
            f"<QwenBackend {self.base_dir.name} lora={self.use_lora} "
            f"4bit={self.load_in_4bit} {state}>"
        )
