"""storage_monitor — 磁盘与显存监控工具

实时报告系统资源占用情况，确保训练/推理不超出硬件限制。
"""

import logging
import shutil
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def get_disk_usage(path: str = ".") -> Dict[str, float]:
    """获取指定路径的磁盘使用情况。

    Args:
        path: 要检查的路径。

    Returns:
        包含 total_gb, used_gb, free_gb, percent 的字典。
    """
    usage = shutil.disk_usage(path)
    return {
        "total_gb": round(usage.total / (1024 ** 3), 2),
        "used_gb": round(usage.used / (1024 ** 3), 2),
        "free_gb": round(usage.free / (1024 ** 3), 2),
        "percent": round(usage.used / usage.total * 100, 1),
    }


def get_gpu_memory() -> Optional[Dict[str, float]]:
    """获取 GPU 显存使用情况（需要 nvidia-smi 可用）。

    Returns:
        包含 total_mb, used_mb, free_mb 的字典，
        若 GPU 不可用则返回 None。
    """
    try:
        import pynvml
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        info = pynvml.nvmlDeviceGetMemoryInfo(handle)
        pynvml.nvmlShutdown()
        return {
            "total_mb": round(info.total / (1024 ** 2), 1),
            "used_mb": round(info.used / (1024 ** 2), 1),
            "free_mb": round(info.free / (1024 ** 2), 1),
        }
    except Exception:
        return None


def check_resources(
    data_dir: str = "./data",
    min_disk_free_gb: float = 10.0,
    min_gpu_free_mb: float = 2000.0,
) -> Dict[str, bool]:
    """检查系统资源是否满足最低要求。

    Args:
        data_dir: 数据目录路径。
        min_disk_free_gb: 最低可用磁盘空间（GB）。
        min_gpu_free_mb: 最低可用 GPU 显存（MB）。

    Returns:
        {"disk_ok": bool, "gpu_ok": bool}，True 表示资源充足。
    """
    result = {"disk_ok": True, "gpu_ok": True}

    # 磁盘检查
    disk = get_disk_usage(data_dir)
    logger.info(
        "磁盘: %.1f/%.1f GB (%.1f%% 已用), 剩余 %.1f GB",
        disk["used_gb"], disk["total_gb"], disk["percent"], disk["free_gb"],
    )
    if disk["free_gb"] < min_disk_free_gb:
        logger.warning(
            "磁盘空间不足！剩余 %.1f GB < %.1f GB 最低要求",
            disk["free_gb"], min_disk_free_gb,
        )
        result["disk_ok"] = False

    # 显存检查
    gpu = get_gpu_memory()
    if gpu is not None:
        logger.info(
            "GPU 显存: %.0f/%.0f MB, 剩余 %.0f MB",
            gpu["used_mb"], gpu["total_mb"], gpu["free_mb"],
        )
        if gpu["free_mb"] < min_gpu_free_mb:
            logger.warning(
                "GPU 显存不足！剩余 %.0f MB < %.0f MB 最低要求",
                gpu["free_mb"], min_gpu_free_mb,
            )
            result["gpu_ok"] = False
    else:
        logger.info("GPU 不可用（未检测到 NVIDIA 设备或 pynvml）")

    return result


def report() -> dict:
    """生成完整的资源状态报告。

    Returns:
        包含磁盘和 GPU 状态的字典，可直接转为 JSON 供前端展示。
    """
    disk = get_disk_usage()
    gpu = get_gpu_memory()

    report_data = {
        "disk": disk,
        "gpu": gpu,
        "timestamp": None,
    }
    return report_data
