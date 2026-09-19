import sqlite3
import subprocess
import sys

import pytest

from virtual_you.backend.store import Store


@pytest.mark.parametrize("version", [(3, 51, 0), (3, 51, 1)])
def test_known_deadlocking_runtime_fails_before_opening_database(tmp_path, monkeypatch, version):
    monkeypatch.setattr(sqlite3, "sqlite_version_info", version)
    with pytest.raises(RuntimeError, match="concurrent-connection deadlock"):
        Store(tmp_path / "blocked.sqlite3")
    assert not (tmp_path / "blocked.sqlite3").exists()


def test_concurrent_wal_connections_finish_and_keep_data(tmp_path):
    # Isolate the regression: a native SQLite deadlock must time out, not hang pytest.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
from virtual_you.backend.store import Store
store = Store(Path(sys.argv[1]))
def cycle(i):
    for j in range(50):
        store.set_metadata(str(i), {'value': j})
        assert store.metadata(str(i))['value'] == j
with ThreadPoolExecutor(max_workers=8) as pool:
    list(pool.map(cycle, range(8)))
with store.connection() as db:
    assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
""",
            str(tmp_path / "concurrent.sqlite3"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
