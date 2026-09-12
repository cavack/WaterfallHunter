from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from waterfallhunter.core.schema_contract import SchemaContractError
from schema_test_support import migrate_test_database
from waterfallhunter.core.schema_contract import CURRENT_RUNTIME_SCHEMA_VERSION


def test_import_main_does_not_create_or_require_registry_database(tmp_path: Path):
    db_path = tmp_path / "missing-registry.db"
    env = os.environ.copy()
    env["REGISTRY_DB_PATH"] = str(db_path)
    env["LIVE_TRADING_ENABLED"] = "false"
    inherited_paths = [entry for entry in sys.path if entry]
    existing_pythonpath = env.get("PYTHONPATH")
    if existing_pythonpath:
        inherited_paths.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(inherited_paths)
    code = "import waterfallhunter.main"

    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert db_path.exists() is False


def test_startup_rejects_missing_schema_before_starting_background_work(
    tmp_path: Path,
    monkeypatch,
):
    import waterfallhunter.main as main

    missing_db = tmp_path / "missing-startup.db"
    started = []

    def record_background(coro):
        started.append(coro)
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        return None

    monkeypatch.setattr(main.db, "db_path", str(missing_db))
    monkeypatch.setattr(main, "_start_background_task", record_background)

    with pytest.raises(SchemaContractError):
        asyncio.run(main.startup_event())

    assert started == []
    assert missing_db.exists() is False


def test_startup_rejects_incompatible_schema_before_background_work(
    tmp_path: Path,
    monkeypatch,
):
    import waterfallhunter.main as main

    db_path = migrate_test_database(tmp_path / "incompatible-startup.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"PRAGMA user_version={CURRENT_RUNTIME_SCHEMA_VERSION + 1}"
        )
    started = []

    def record_background(coro):
        started.append(coro)
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        return None

    monkeypatch.setattr(main.db, "db_path", str(db_path))
    monkeypatch.setattr(main, "_start_background_task", record_background)

    with pytest.raises(SchemaContractError):
        asyncio.run(main.startup_event())

    assert started == []
    assert db_path.exists() is True
