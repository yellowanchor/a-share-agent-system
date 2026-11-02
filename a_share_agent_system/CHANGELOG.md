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

## [0.3.0] — 2026-11-02　Phase 3.1：三 Agent 工作流

### 新增

- **`src/agents/analyst.py`**：舆情研判 Agent。RAG 语义检索（bge-m3 + LocalVectorStore）
  → 词典弱监督逐条打标 → 证据编号（`[证据 n]`）→ LLM 综合成研判初稿。
  财务严格按 `pub_date ≤ as_of` 做 Point-in-Time 对齐；LLM 不可用时自动降级为规则路径
- **`src/agents/strategist.py`**：策略 Agent。先用 ATR/支撑阻力**程序化**算出做多/做空
  两套参考价位，再让模型在其附近取值，避免数值幻觉；仓位强制收敛到 0–20%
- **`src/agents/critic.py`**：双层校验 Agent。第一层 13 项确定性规则
  （E001–E003 / P001–P002 / C001–C002 / N001–N004 / T001–T002），可复现；
  第二层大模型语义校验，结论标注 `source=llm` 且**仅供参考**
- **`src/agents/schemas.py`**：Agent 间数据契约（Evidence / AnalystReport /
  StrategyAdvice / CriticVerdict），用 Pydantic 固定，防止上游格式漂移
- **`src/models/llm/`**：LLM 后端抽象，`QwenBackend`（4bit NF4，懒加载 + 可释放）
  与 `MockLLMBackend`（离线桩，按 role 路由），Agent 层不感知具体模型
- **`src/utils/indicators.py`**：纯 pandas 技术指标（MA/EMA/RSI/ATR/量比/年化波动率/
  支撑阻力），不引入 TA-Lib；含解析解单元测试
- **`src/utils/json_utils.py`**：LLM 输出 JSON 的五级容错解析（直解 / 剥围栏 /
  截取 `{...}` / 修中文引号尾逗号 / 截断补救）
- **`scripts/run_agent_demo.py`**：端到端演示，输出带证据链的 Markdown 报告
- **`tests/test_agents.py`**：28 项测试（Golden Set 词典回归 / 指标解析解 /
  JSON 容错 / Mock 路由 / Analyst 真实数据 / 参考价位 / Critic 规则触发 / 降级路径）
- `configs/prompts/` 三份提示词重写：补充证据引用规范、方向约束、校验清单与输出 schema

### 实测发现（论文素材）

1. **大模型数值方向错误**：首次真实推理中，模型照抄做多口径参考价却判了做空方向，
   止损/止盈方向全反，被 Critic 的 N002/N003 拦截。修复：参考价位拆成多/空两套并绑定方向
2. **语义校验层自身会出错**：LLM 曾建议「把做空止损调到低于收盘价」（该建议本身错误）。
   故语义层结论只作参考，判定权归规则层与人工
3. Mock 后端按提示词关键词路由不可靠（Analyst 提示词含「量化策略」字样会误判），
   改为按显式 `role` 参数路由

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
