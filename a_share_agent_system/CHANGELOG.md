# 更新日志

本项目遵循语义化版本（Semantic Versioning）。

## [0.5.0] — 2026-11-02　Phase 3.3：Streamlit 前端看板

### 新增

- **`src/app/` 五模块看板**（苹果官网简约风格）：
  - `main.py`：主入口。侧边栏批次/股票选择器，主区按「概览 → K线 → 情感趋势 →
    Agent 报告 → 证据链 → 迭代闭环 → 全部股票」编排；`md()` 统一 HTML 注入
    （逐行 strip，规避 markdown-it 把缩进 HTML 行误解析成代码块导致标签泄漏）
  - `style.py`：全局 CSS——#f5f5f7 区块底色、#d2d2d7 发丝分隔线、18px 圆角卡片、
    系统字体栈、A 股红涨绿跌配色
  - `data.py`：只读数据访问层，全部挂 `@st.cache_data`（batch JSON / DuckDB 前复权
    日线 / 情感标签 parquet / 股票名称映射）
  - `charts.py`：Plotly 图表——K 线+成交量+MA+策略价位线+PIT 截止日竖线、
    日频利多/利空条形+7 日净情绪均线、首轮 vs 终局通过率对比
  - `views.py`：Analyst/Strategist/Critic 卡片、行情/财务快照、证据链、
    迭代闭环时间线、全部股票表格的纯函数 HTML 渲染
- 看板为**纯只读展示层**：不加载 LLM / 不触发推理，仅读取 Phase 3.2 评测产物

### 修复

- plotly 在类目轴上 `add_vline(annotation_text=...)` 会对字符串求均值而崩溃，
  拆成「vline（无标注）+ add_annotation（显式定位）」
- plotly 5.24 不支持 `rangeselector=False` 布尔值，移除该参数
- `st.markdown` 多行缩进 HTML 泄漏：统一在 `md()` 帮助函数中清洗
- `use_container_width` 在 streamlit 1.52 弃用，改用 `width="stretch"`

## [0.4.0] — 2026-11-02　Phase 3.2：编排层与迭代闭环

### 新增

- **`src/agents/orchestrator.py`**：三 Agent 工作流编排层。Analyst 只跑一次
  （证据客观，重跑无意义），Strategist → Critic 构成**带反馈的迭代闭环**：
  Critic 不通过时把问题列表注入下一轮提示词并下调温度重试。
  四种终局：`first_pass` / `retry_pass`（采纳自动修正）/ `revised` / `fallback`
  （降级为观望 + 0 仓位）；连续两轮问题集合相同则提前终止，避免空耗算力
- **`src/agents/stats.py`**：批量评测指标统计——首轮/终局通过率、闭环收敛增益、
  规则触发频次（首轮 vs 终局残留）、终局来源分布、稳态耗时（剔除首只模型加载）
- **`src/agents/report.py`**：Markdown 渲染单一来源（单只报告 + 批量报告共用，
  避免两处版式漂移），报告含「迭代收敛过程」章节
- **`scripts/batch_eval.py`**：多股票批量评测。复用同一批 Agent 实例
  （bge-m3 / Qwen 只加载一次），输出 Markdown 报告 + 结构化 JSON；
  `--max-position-pct` 可收紧仓位上限做闭环压力测试
- **`tests/test_orchestrator.py`**：13 项测试，用脚本化 Strategist 确定性覆盖
  全部闭环分支（首轮通过 / 重试收敛 / 修正采纳 / 降级 / 重复提前终止 / 批量容错）

### 变更

- `StrategistAgent.run` 支持 `feedback`（注入 Critic 问题清单）与 `temperature`
  （重试时收敛）参数；提示词明确要求「只修正问题项，其余字段沿用上一轮」，
  防止模型另起炉灶把正确的方向也改掉
- `CriticAgent` 新增公开方法 `check_rules()`：只跑规则层的复检接口，
  供编排层验证自动修正版是否真的合规（修正本身也要被验证）
- `scripts/run_agent_demo.py` 改走编排层（`--max-retries 0` 可退回单向流水线做对照）
- `configs/settings.yaml` 新增 `orchestrator` 配置段

### 实测（Qwen 4bit + bge-m3，截止日 2026-08-20）

**常规批次（8 只跨行业，仓位上限 20%）**：

- 8/8 首轮通过（提示词改进后策略质量稳定），方向分布：做空 2 / 做多 4 / 观望 2
- Agent 复用效果：首只 14.3s（含 bge-m3 加载），后续单只检索仅约 50ms；
  稳态单只全流程约 9–12s；整批 8 只 2m29s

**风控收紧批次（4 只，生成侧上限 20% vs 校验侧上限 8%）**——模拟
「风控标准比策略生成更严」的真实场景，观察闭环能否收敛：

- 首轮通过率 **25%** → 终局通过率 **100%**（闭环收敛增益 **+75%**），
  平均轮次 2.0，终局分布：retry_pass 2 / revised 1 / first_pass 1
- 实测发现（论文素材）：反馈重试虽然能修掉指定问题，但模型可能
  **连带改变原本正确的字段**（茅台案例中重试把做空改成了做多——过了
  规则校验，但方向与首轮舆情判断相悖）；且重试输出并不单调收敛
  （通威第 3 轮仓位又回到 20%）。结论：**确定性自动修正（revised）
  作为最终兜底不可少**，纯靠模型重试不可靠。

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
