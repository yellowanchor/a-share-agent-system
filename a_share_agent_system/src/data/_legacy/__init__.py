"""_legacy — 早期数据采集框架归档区（已停止维护）

背景
----
2026-06 搭建的第一版采集框架，设计目标是用统一的 `BaseFetcher` 抽象
同时接入 A 股 / 港股 / 新闻三类数据源，并配合 `validators` 做入库质检。

该框架**从未实际产出过数据**。Phase 1 的真实数据基座（1755 万行行情、
32 万行财务、12.6 万条新闻）由独立项目 `data_platform/` 完成，
采用「编号脚本 + baostock_client + storage」的轻量分片方案，
在断点续传与并发控制上更贴合 baostock/新浪接口的实际情况。

保留原因
--------
作为设计演进记录保留，用于说明"统一抽象层 → 轻量分片"的技术选型过程。
**不应被新代码引用**，新数据接入请走 `data_platform/`。

内容
----
- `fetchers/`     统一采集抽象层（BaseFetcher / AShareFetcher / HKStockFetcher /
                  NewsFetcher / MarketDataPipeline）
- `validators/`   数据质检与质量报告生成（DataValidator / QualityReport）
"""
