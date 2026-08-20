# A股多智能体舆情与量化策略系统

> 毕业设计项目 — 基于 LLM 多智能体框架的 A 股舆情分析与量化策略生成系统

## 项目简介

本系统通过多智能体协作（分析师 / 策略师 / 评审员）对 A 股市场进行舆情分析，结合 RAG 检索增强生成与量化策略，为投资决策提供辅助参考。

### 核心模块

| 模块 | 说明 |
|------|------|
| **数据采集** | 全 A 股行情（5443 只，含退市 232 只）、财务报表、金融新闻（126K 条） |
| **数据底座** | DuckDB 列式存储（3555 万行）+ 自研向量检索（LocalVectorStore） |
| **RAG 检索** | bge-m3 嵌入模型 + GPU 精确余弦检索，端到端 111ms |
| **多智能体** | Analyst / Strategist / Critic 三 Agent 协作（Phase 2 开发中） |
| **情感分析** | 基于新闻文本的情感分类模型（Phase 2 开发中） |
| **前端展示** | Streamlit 交互式看板（Phase 3 开发中） |

## 目录结构

```
毕业设计/
├── a_share_agent_system/      # 主系统
│   ├── src/                   # 核心代码
│   │   ├── agents/            # 多智能体框架
│   │   ├── data/              # 数据采集与存储
│   │   │   ├── fetchers/      # 数据获取器
│   │   │   ├── storage/       # DuckDB 查询接口
│   │   │   └── validators/   # 数据质量校验
│   │   ├── models/            # 模型
│   │   │   └── rag/           # RAG 检索（LocalVectorStore）
│   │   └── utils/             # 工具函数
│   ├── scripts/               # 构建/验证脚本
│   ├── configs/               # 配置文件
│   ├── tests/                 # 测试
│   └── requirements.txt       # Python 依赖
├── data_platform/             # 数据采集平台
│   ├── scripts/               # 采集脚本
│   ├── src/                   # 采集器源码
│   └── 数据集说明.md           # 数据集文档
├── 项目说明书.md               # 项目说明书
├── 项目分析报告.md             # 项目分析报告
├── 工作步骤.md                 # 工作步骤
├── 数据获取.md                 # 数据获取方案
└── 项目总结/                   # 阶段性总结
```

## 技术栈

| 类别 | 技术 |
|------|------|
| 语言 | Python 3.13 |
| 数据存储 | DuckDB 1.5.3（列式，内存模式） |
| 向量检索 | 自研 LocalVectorStore（bge-m3 + torch GPU） |
| 嵌入模型 | BAAI/bge-m3（1024 维，560M 参数） |
| 大语言模型 | Qwen2.5-7B-Instruct（Phase 2） |
| 深度学习 | PyTorch 2.11.0+cu128 |
| 前端 | Streamlit |
| 数据源 | AkShare / BaoStock / 新浪财经 |

## 快速开始

```bash
# 安装依赖
cd a_share_agent_system
pip install -r requirements.txt

# 构建 DuckDB 行情库
python scripts/build_market_db.py

# 构建向量检索索引
python scripts/build_rag_index.py

# 端到端验证
python scripts/validate_rag.py
```

## 开发阶段

- [x] **Phase 1** — 数据底座：数据采集 + DuckDB 建库 + RAG 向量检索（已完成）
- [ ] **Phase 2** — 模型训练：情感分析 + Qwen2.5-7B LoRA 微调
- [ ] **Phase 3** — 多智能体框架 + Streamlit 前端

## 硬件环境

- GPU: RTX 5070 Ti 16GB
- RAM: 64GB
- OS: Windows 11

## 声明

本项目为学术研究用途，不构成投资建议。数据来源于公开渠道（AkShare、BaoStock、新浪财经等）。
