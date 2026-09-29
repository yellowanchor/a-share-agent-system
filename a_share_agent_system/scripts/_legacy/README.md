# `_legacy/` — 一次性验证脚本归档区

均为 2026-06 项目启动期的冒烟验证脚本，依赖 `src/data/_legacy/` 下的早期采集框架，
**已不再参与任何流水线**，仅作历史记录保留。

| 脚本 | 原用途 |
|------|--------|
| `run_test.py` | 早期框架的基础连通性冒烟测试 |
| `run_phase1_test.py` | Phase 1 采集流程验证 |
| `fetch_training_data.py` | 早期行情 + 财务拉取入口 |
| `fetch_news_training_data.py` | 早期新闻拉取入口 |

现行替代方案：

- 数据采集 → `data_platform/scripts/01~06_*.py`
- 入库 → `scripts/build_market_db.py`
- RAG 建库 → `scripts/build_rag_index.py`
- 端到端验证 → `scripts/validate_rag.py`
