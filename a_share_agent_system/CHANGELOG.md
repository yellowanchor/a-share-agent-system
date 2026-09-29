# 更新日志

本项目遵循语义化版本（Semantic Versioning）。

## [0.2.0] — 2026-09-29　工程化重构

### 新增

- **`pyproject.toml`**：PEP 621 标准元数据，src-layout 包发现（顶层包名 `src`），
  依赖分 `core` / `train` / `app` / `dev` 四组 extras；内置 ruff、pytest、mypy 配置
- **`src/config.py`**：统一配置入口，从 `configs/settings.yaml` 读取路径与超参，
  支持 `ASARE_HOME` 环境变量覆盖，消除散落各脚本的硬编码
- **`Makefile`**：`install` / `lint` / `format` / `test` / `db` / `rag` / `train` / `eval` 命令入口
- **`.editorconfig`**、**`.pre-commit-config.yaml`**、**`.env.example`**：开发工具链与环境变量样例
- 测试体系规划：`tests/` 补齐 4 组用例（词典 / 向量库 / DuckDB / Agent 编排）

### 变更

- 包可安装化：`pip install -e .[dev]` 后 `import src.*` 全局可用，不再依赖工作目录
- `.gitignore` 补充工具缓存（`.ruff_cache` / `.mypy_cache` / `.pytest_cache`）与覆盖率产物

### 移除

- 根目录 17 个一次性文档排版脚本（开题报告 docx / 答辩 PPT / 答辩稿，共 311 KB）
- 根目录 4 个早期验证脚本 → 归档至 `scripts/_legacy/`
- `data/_backup/` 359 MB（ChromaDB 废弃索引 + HNSW 测试残留，已被自研 LocalVectorStore 取代）

### 归档

- `src/data/fetchers/`、`src/data/validators/` → `src/data/_legacy/`
  （2026-06 早期采集框架，从未产出数据；Phase 1 实际由 `data_platform/` 完成）
- `gen_figs.py`、`gen_ppt_figs.py` → 项目根目录 `论文资产/`（论文插图仍需微调）

## [0.1.0] — 2026-09-12　Phase 1 + Phase 2

### Phase 1 — 数据底座

- DuckDB 入库 3555 万行（27s）；行情 1755 万行（前复权 + 不复权双份，1990–2026）
- 财务 32 万行按 `pubDate` 做 Point-in-Time 对齐
- 新闻 12.6 万条（SimHash 去重）
- 自研 `LocalVectorStore` 替代 ChromaDB（规避 Windows HNSW 不落盘缺陷），端到端 111ms

### Phase 2 — 情感分析

- 金融情感词典 v3，人工校验一致率 90.5%
- QLoRA NF4 微调 Qwen2.5-7B，准确率 89.0% / Macro-F1 0.858，利空召回 100%
- 三组对比：词典 90.5% > SFT 89.0% > SFT+RAG 82.0% > base 79.5%
- RAG 检索：Recall@5 0.842 / MRR@10 0.809
- 增量更新机制（md5 幂等 + 原子落盘）
