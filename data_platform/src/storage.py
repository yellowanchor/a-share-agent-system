# -*- coding: utf-8 -*-
"""
数据存储层
==========
统一管理所有数据集的读写：
  - Parquet 分片存储（写入快、断点续传友好）
  - DuckDB 查询层（千万级数据秒级过滤）
  - 进度记录（断点续传标记）

目录约定
--------
data/
├── raw/          原始抓取数据（分片 parquet）
├── processed/    合并后的训练数据集（parquet + duckdb）
├── logs/         运行日志
└── progress/     采集进度标记（json）
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

# 数据平台根目录 = data_platform/
DATA_ROOT = Path(__file__).resolve().parent.parent / "data"
RAW_DIR = DATA_ROOT / "raw"
PROCESSED_DIR = DATA_ROOT / "processed"
LOG_DIR = DATA_ROOT / "logs"
PROGRESS_DIR = DATA_ROOT / "progress"

logger = logging.getLogger(__name__)


def ensure_dirs() -> None:
    """确保所有数据目录存在"""
    for d in (RAW_DIR, PROCESSED_DIR, LOG_DIR, PROGRESS_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# 分片读写
# ---------------------------------------------------------------------------

def save_shard(df: pd.DataFrame, name: str, shard_idx: int,
               subdir: str = "raw") -> Path:
    """
    保存一个分片 parquet 文件。
    分片文件命名: {name}_part{idx:03d}.parquet
    """
    d = (RAW_DIR if subdir == "raw" else PROCESSED_DIR)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}_part{shard_idx:03d}.parquet"
    df.to_parquet(path, index=False)
    logger.info("分片已保存: %s (%d 行)", path.name, len(df))
    return path


def load_shards(name: str, subdir: str = "raw") -> pd.DataFrame:
    """
    读取某数据集的全部分片并合并。
    找不到任何分片时返回空 DataFrame。
    """
    d = (RAW_DIR if subdir == "raw" else PROCESSED_DIR)
    parts = sorted(d.glob(f"{name}_part*.parquet"))
    if not parts:
        return pd.DataFrame()
    frames = [pd.read_parquet(p) for p in parts]
    df = pd.concat(frames, ignore_index=True)
    logger.info("合并 %d 个分片: %s (%d 行)", len(parts), name, len(df))
    return df


def merge_shards(name: str, subdir: str = "raw",
                 final_name: str | None = None) -> Path:
    """
    将某数据集的全部分片合并为一个最终 parquet（processed 目录），
    去重后返回最终文件路径。
    """
    df = load_shards(name, subdir=subdir)
    if df.empty:
        raise FileNotFoundError(f"数据集 {name} 无任何分片")
    final_name = final_name or name
    # 全列去重（同一只股票同一日期只会保留一条）
    df = df.drop_duplicates()
    out = PROCESSED_DIR / f"{final_name}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    logger.info("最终合并: %s (%d 行, 去重后)", out, len(df))
    return out


# ---------------------------------------------------------------------------
# 进度标记（断点续传）
# ---------------------------------------------------------------------------

def save_progress(name: str, done_items: set | list) -> None:
    """记录已完成项（如已抓取的股票代码），用于断点续传"""
    PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
    path = PROGRESS_DIR / f"{name}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(done_items), f, ensure_ascii=False)


def load_progress(name: str) -> set:
    """读取已完成项；不存在时返回空集合"""
    path = PROGRESS_DIR / f"{name}.json"
    if not path.exists():
        return set()
    with open(path, "r", encoding="utf-8") as f:
        return set(json.load(f))


# ---------------------------------------------------------------------------
# DuckDB 查询层
# ---------------------------------------------------------------------------

def register_tables() -> None:
    """
    把 processed 目录下所有 parquet 注册到 DuckDB（内存实例返回前需保持引用）。
    注意：返回的连接对象必须由调用方持有，否则表会失效。
    """
    import duckdb

    con = duckdb.connect()
    for p in sorted(PROCESSED_DIR.glob("*.parquet")):
        tbl = p.stem
        try:
            con.execute(f"CREATE OR REPLACE VIEW {tbl} AS SELECT * FROM '{p}'")
        except Exception as e:  # noqa: BLE001
            logger.warning("注册 %s 失败: %s", p.name, e)
    return con


def query(sql: str, con=None) -> pd.DataFrame:
    """对已注册表执行 SQL，返回 DataFrame"""
    import duckdb

    if con is None:
        con = duckdb.connect()
        for p in sorted(PROCESSED_DIR.glob("*.parquet")):
            tbl = p.stem
            con.execute(f"CREATE OR REPLACE VIEW {tbl} AS SELECT * FROM '{p}'")
    return con.execute(sql).df()
