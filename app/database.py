"""数据库引擎、会话生命周期和本地建表入口。"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    """全部 ORM 实体共享的 SQLAlchemy 声明式基类。"""

    pass


def _ensure_sqlite_directory(database_url: str) -> None:
    """仅为文件型 SQLite 地址创建父目录，其他数据库地址保持原样。"""

    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return
    path = database_url.removeprefix(prefix)
    if path == ":memory:":
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_directory(settings.database_url)

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """为每个 HTTP 请求提供独立会话，并在响应后可靠关闭连接。"""

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_schema() -> None:
    """为全新 MVP 环境建表；正式版本应改用 Alembic 增量迁移。"""

    # 延迟导入确保全部实体已注册到 Base.metadata，同时避免模块循环依赖。
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
