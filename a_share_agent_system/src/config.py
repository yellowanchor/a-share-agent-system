"""config.py — 统一配置与路径入口

设计目的
--------
消除散落在各脚本中的硬编码路径与超参数。所有目录定位、模型名称、
Agent 温度等一律从 `configs/settings.yaml` 读取，脚本只关心业务逻辑。

用法
----
    from src.config import cfg, PROCESSED_DIR, get

    df = pd.read_parquet(PROCESSED_DIR / "sentiment_labeled.parquet")
    temperature = cfg.agents.analyst.temperature

环境变量覆盖
------------
支持 `ASARE_HOME` 覆盖项目根目录（默认自动推断），
敏感信息（API Key）一律经环境变量注入，不写入 YAML。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import yaml

__all__ = [
    "PROJECT_ROOT",
    "CONFIG_DIR",
    "DATA_DIR",
    "PROCESSED_DIR",
    "VECTOR_STORE_DIR",
    "LOGS_DIR",
    "MODELS_DIR",
    "settings",
    "cfg",
    "get",
    "load_yaml",
]


# ------------------------------------------------------------------
# 目录定位
# ------------------------------------------------------------------
def _find_root() -> Path:
    """推断项目根目录（a_share_agent_system/）。

    优先级：环境变量 ASARE_HOME > 本文件向上三级（src/config.py -> src -> root）
    """
    env = os.environ.get("ASARE_HOME")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parent.parent


PROJECT_ROOT: Path = _find_root()
CONFIG_DIR: Path = PROJECT_ROOT / "configs"
DATA_DIR: Path = PROJECT_ROOT / "data"
PROCESSED_DIR: Path = DATA_DIR / "processed"
VECTOR_STORE_DIR: Path = DATA_DIR / "vector_store"
LOGS_DIR: Path = PROJECT_ROOT / "logs"
MODELS_DIR: Path = PROJECT_ROOT / "models"

# 确保运行时目录存在
LOGS_DIR.mkdir(parents=True, exist_ok=True)


# ------------------------------------------------------------------
# YAML 加载
# ------------------------------------------------------------------
class _AttrDict(dict):
    """支持属性访问的字典，便于 `cfg.agents.analyst.temperature` 链式取值。"""

    def __getattr__(self, item: str) -> Any:
        try:
            value = self[item]
        except KeyError as exc:  # pragma: no cover - 配置缺失时给出明确提示
            raise AttributeError(f"配置项不存在: {item}") from exc
        if isinstance(value, dict) and not isinstance(value, _AttrDict):
            value = _AttrDict(value)
            self[item] = value
        return value

    def __setattr__(self, key: str, value: Any) -> None:  # pragma: no cover
        self[key] = value


def load_yaml(path: Path) -> _AttrDict:
    """加载 YAML 文件为属性字典；文件不存在时返回空字典。"""
    if not path.exists():
        return _AttrDict()
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return _AttrDict(data)


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """递归合并两个字典，override 优先。"""
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_settings() -> _AttrDict:
    """加载主配置，并合并 train_config.yaml 与 prompts/*.yaml。"""
    main = load_yaml(CONFIG_DIR / "settings.yaml")

    # 训练配置并入 settings.training
    train = load_yaml(CONFIG_DIR / "train_config.yaml")
    if train:
        merged = _deep_merge(dict(main), {"training": dict(train)})
        main = _AttrDict(merged)

    # Agent 提示词并入 settings.prompts
    prompts: Dict[str, Any] = {}
    prompt_dir = CONFIG_DIR / "prompts"
    if prompt_dir.exists():
        for f in sorted(prompt_dir.glob("*.yaml")):
            prompts[f.stem] = dict(load_yaml(f))
    if prompts:
        merged = _deep_merge(dict(main), {"prompts": prompts})
        main = _AttrDict(merged)

    return main


#: 全局配置对象
settings: _AttrDict = _load_settings()

#: 别名，便于 `from src.config import cfg` 后链式访问
cfg = settings


def get(dotted: str, default: Any = None) -> Any:
    """按点分路径取值，避免长链属性访问时抛异常。

    Example
    -------
    >>> get("agents.analyst.temperature", 0.1)
    0.1
    """
    node: Any = settings
    for part in dotted.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return default
    return node


if __name__ == "__main__":  # pragma: no cover - 自检入口
    print(f"PROJECT_ROOT     = {PROJECT_ROOT}")
    print(f"PROCESSED_DIR    = {PROCESSED_DIR}")
    print(f"VECTOR_STORE_DIR = {VECTOR_STORE_DIR}")
    print(f"llm.name         = {get('models.llm.name')}")
    print(f"embedding.name   = {get('models.embedding.name')}")
    print(f"analyst.temp     = {get('agents.analyst.temperature')}")
    print(f"prompts loaded   = {list(get('prompts', {}).keys())}")
