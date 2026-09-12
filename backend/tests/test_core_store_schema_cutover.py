from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from schema_test_support import migrate_test_database
from waterfallhunter.core.db import DBAdapter
from waterfallhunter.core.lbank_signal_ledger import LBankSignalLedger
from waterfallhunter.core.lbank_signal_outcome import LBankSignalOutcomeStore
from waterfallhunter.core.schema_contract import SchemaContractError
from waterfallhunter.core.stage_lifecycle import StageLifecycleStore


STORE_TYPES = (
    DBAdapter,
    StageLifecycleStore,
    LBankSignalLedger,
    LBankSignalOutcomeStore,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("store_type", STORE_TYPES)
def test_core_store_default_verification_rejects_missing_database_without_creating(
    tmp_path: Path,
    store_type,
):
    db_path = tmp_path / f"{store_type.__name__}.db"

    with pytest.raises(SchemaContractError):
        store_type(str(db_path))

    assert db_path.exists() is False


@pytest.mark.parametrize("store_type", STORE_TYPES)
def test_core_store_verify_schema_false_does_not_create_database(
    tmp_path: Path,
    store_type,
):
    db_path = tmp_path / f"{store_type.__name__}.db"

    store = store_type(str(db_path), verify_schema=False)

    assert store.db_path == str(db_path)
    assert db_path.exists() is False


@pytest.mark.parametrize("store_type", STORE_TYPES)
def test_core_store_verify_schema_false_does_not_mutate_existing_empty_file(
    tmp_path: Path,
    store_type,
):
    db_path = tmp_path / f"{store_type.__name__}.db"
    db_path.write_bytes(b"")
    before = _sha256(db_path)

    store_type(str(db_path), verify_schema=False)

    assert _sha256(db_path) == before


@pytest.mark.parametrize("store_type", STORE_TYPES)
def test_core_store_default_verification_accepts_migrated_schema(
    tmp_path: Path,
    store_type,
):
    db_path = migrate_test_database(tmp_path / f"{store_type.__name__}.db")
    before = _sha256(db_path)

    store = store_type(str(db_path))

    assert store.db_path == str(db_path)
    assert _sha256(db_path) == before


@pytest.mark.parametrize(
    ("store_type", "dependency_table"),
    (
        (StageLifecycleStore, "lbank_catalog"),
        (LBankSignalLedger, "lbank_catalog"),
        (LBankSignalOutcomeStore, "lbank_signal_ledger"),
    ),
)
def test_core_store_verification_rejects_missing_query_dependency(
    tmp_path: Path,
    store_type,
    dependency_table: str,
):
    db_path = migrate_test_database(tmp_path / f"{store_type.__name__}.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(f'DROP TABLE "{dependency_table}"')

    with pytest.raises(SchemaContractError):
        store_type(str(db_path))
