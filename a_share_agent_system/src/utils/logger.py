"""logger — 统一日志管理

提供项目级日志配置，支持：
    - 控制台 + 文件双输出
    - 按模块分级别控制
    - 日志轮转（防磁盘占满）
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(
    level: str = "INFO",
    log_file: str = "./logs/system.log",
    max_bytes: int = 50 * 1024 * 1024,  # 50MB
    backup_count: int = 5,
) -> None:
    """配置全局日志系统。

    Args:
        level: 日志级别（DEBUG/INFO/WARNING/ERROR）。
        log_file: 日志文件路径。
        max_bytes: 单个日志文件最大字节数。
        backup_count: 保留的历史日志文件数量。
    """
    # 确保日志目录存在
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # 根日志器配置
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # 日志格式
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台输出
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # 文件输出（带轮转）
    file_handler = RotatingFileHandler(
        log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # 抑制过于嘈杂的第三方库日志
    for noisy_lib in ("urllib3", "matplotlib", "PIL"):
        logging.getLogger(noisy_lib).setLevel(logging.WARNING)

    logging.getLogger(__name__).info("日志系统初始化完成 (级别=%s)", level)
