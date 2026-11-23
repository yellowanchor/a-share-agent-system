"""数据质量校验模块

提供行情数据完整性、时效性、合理性校验和结构化报告生成。
"""

from src.data._legacy.validators.data_validator import DataValidator, DataValidationError
from src.data._legacy.validators.quality_report import QualityReport

__all__ = ["DataValidator", "DataValidationError", "QualityReport"]
