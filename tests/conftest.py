"""
Shared test fixtures.

Database tests run against a SEPARATE database, `esam_test`, on the same
docker-compose server. They never touch the lesson data in `esam_pilot`.
The database is created on first use. If the server isn't running, those
tests are skipped (not failed), so pure-Python tests always run.

    docker compose up -d --wait
    pytest -q
"""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from esamkit.config import settings  # noqa: E402

TEST_DB = "esam_test"


def _test_dsn() -> str:
    if os.getenv("ESAM_TEST_PG_DSN"):
        return os.environ["ESAM_TEST_PG_DSN"]
    base, _, _ = settings.pg_dsn.rpartition("/")
    return f"{base}/{TEST_DB}"


@pytest.fixture(scope="session")
def pg_dsn() -> str:
    psycopg = pytest.importorskip("psycopg")
    try:
        admin = psycopg.connect(settings.pg_dsn, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not reachable ({e.__class__.__name__}); run `docker compose up -d --wait`")
    with admin:
        exists = admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,)).fetchone()
        if not exists:
            admin.execute(f"CREATE DATABASE {TEST_DB}")
    return _test_dsn()


@pytest.fixture(scope="session")
def baseline():
    from esamkit.baseline import load_baseline
    return load_baseline(settings.baseline_yaml)
