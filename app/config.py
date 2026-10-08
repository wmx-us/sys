"""应用配置。

配置只从环境变量读取，并为本地演示提供安全边界明确的默认值。生产部署必须
覆盖 ``SECRET_KEY`` 和 ``DATABASE_URL``。
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _as_bool(value: str) -> bool:
    return value.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """启动后不可变的应用配置，避免运行过程中被意外修改。"""

    app_name: str = os.getenv("APP_NAME", "科管系统")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./data/keguan.db")
    secret_key: str = os.getenv("SECRET_KEY", "local-dev-secret-change-me")
    session_max_age: int = int(os.getenv("SESSION_MAX_AGE", "28800"))
    debug: bool = _as_bool(os.getenv("DEBUG", "false"))


settings = Settings()
