from __future__ import annotations
import os
from pathlib import Path

PROJECT_NAME = "Dayseal"
API_PORT = 10700


def _data_dir() -> Path:
    #: 测试 / 容器部署允许用环境变量把数据目录指向临时卷；
    #: DAYSEAL_DATA 是 Dockerfile 里的旧变量名，一并兼容。
    raw = os.environ.get("DAYSEAL_DATA_DIR") or os.environ.get("DAYSEAL_DATA")
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[2] / "data"


DATA_DIR = _data_dir()
DB_PATH = DATA_DIR / "dayseal.db"
