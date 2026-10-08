from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


TEST_DB = Path("data/test_keguan.db")
os.environ["DATABASE_URL"] = f"sqlite:///./{TEST_DB}"
os.environ["SECRET_KEY"] = "test-secret-key"

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import ensure_demo_data  # noqa: E402


@pytest.fixture(autouse=True)
def reset_database() -> Generator[None, None, None]:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    ensure_demo_data()
    yield


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client
