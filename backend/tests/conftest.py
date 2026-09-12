from __future__ import annotations

import os
import tempfile
from pathlib import Path

from schema_test_support import migrate_test_database


_RUNTIME_TEST_DIR = tempfile.TemporaryDirectory(prefix="waterfallhunter-tests-")
_RUNTIME_TEST_DB = Path(_RUNTIME_TEST_DIR.name) / "waterfall_registry.db"
migrate_test_database(_RUNTIME_TEST_DB)
os.environ["REGISTRY_DB_PATH"] = str(_RUNTIME_TEST_DB)
