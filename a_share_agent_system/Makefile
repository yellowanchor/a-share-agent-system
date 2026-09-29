# ============================================================
#  常用命令入口（Git Bash / WSL 下执行：make <target>）
#  Windows PowerShell 用户可直接执行 .PHONY 下方对应命令
# ============================================================

PY ?= python
PKG := a_share_agent_system

.PHONY: help install install-train lint format test test-fast clean db rag label train eval

help:                          ## 显示所有可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# -------- 环境 --------
install:                       ## 安装核心依赖（开发模式）
	$(PY) -m pip install -e .[dev]

install-train:                 ## 安装训练依赖（torch 需先手动装 cu128 版）
	$(PY) -m pip install -e .[dev,train]

# -------- 代码质量 --------
lint:                          ## 静态检查（ruff）
	$(PY) -m ruff check src scripts tests

format:                        ## 自动格式化
	$(PY) -m ruff format src scripts tests

test:                          ## 运行全部测试
	$(PY) -m pytest tests -v

test-fast:                     ## 跳过耗时用例
	$(PY) -m pytest tests -v -m "not slow"

# -------- 数据流水线 --------
db:                            ## 构建 DuckDB 行情库
	$(PY) scripts/build_market_db.py

rag:                           ## 构建 RAG 向量索引
	$(PY) scripts/build_rag_index.py

label:                         ## 情感词典打标
	$(PY) scripts/build_sentiment_dataset.py

# -------- 模型 --------
train:                         ## QLoRA 微调（续训加 --resume）
	$(PY) src/models/sentiment/train.py

eval:                          ## base vs SFT 对比评估
	$(PY) src/models/sentiment/evaluate.py

# -------- 清理 --------
clean:                         ## 清理构建与缓存产物
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@rm -rf .pytest_cache .ruff_cache .mypy_cache 2>/dev/null || true
	@echo "cleaned"
