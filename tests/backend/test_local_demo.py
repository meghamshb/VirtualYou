"""The portable demo must not consume a teammate's real provider configuration."""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "local_demo.py"


def run_demo(folder, data, env=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--check", "--data-dir", str(data)],
        cwd=folder,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def test_demo_ignores_real_config_and_preserves_seed_on_repeat(tmp_path):
    (tmp_path / ".env").write_text(
        "VIRTUAL_YOU_LLM_PROVIDER=openai\nVIRTUAL_YOU_LIVE_DELIVERY=true\n"
        "OPENAI_API_KEY=do-not-use-this-test-value\n"
    )
    env = {
        **os.environ,
        "VIRTUAL_YOU_LLM_PROVIDER": "openai",
        "OPENAI_API_KEY": "do-not-use-this-test-value",
        "VIRTUAL_YOU_LIVE_DELIVERY": "true",
        "VIRTUAL_YOU_MCP_JIRA": "true",
        "JIRA_BASE_URL": "https://invalid.example",
        "JIRA_EMAIL": "test@example.invalid",
        "JIRA_API_TOKEN": "do-not-use-this-test-value",
    }
    data = tmp_path / "private-demo"
    first = run_demo(tmp_path, data, env)
    assert first.returncode == 0, first.stderr
    assert "PASS:" in first.stdout
    assert "do-not-use-this-test-value" not in first.stdout + first.stderr
    key = (data / "admin.key").read_text()
    assert key.strip() not in first.stdout + first.stderr
    record = next((data / "activities").glob("activity-*.json")).read_bytes()
    second = run_demo(tmp_path, data, env)
    assert second.returncode == 0, second.stderr
    assert next((data / "activities").glob("activity-*.json")).read_bytes() == record
    assert (data / "admin.key").read_text() == key
    with sqlite3.connect(data / "backend.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM activities").fetchone()[0] == 1
        assert {r[0] for r in db.execute("SELECT status FROM drafts")} == {"pending", "simulated"}
        assert db.execute("SELECT id FROM drafts WHERE status='pending'").fetchall() == [
            ("synthetic-demo-report",)
        ]
        assert all(
            json.loads(r[0])["version"] == 1 for r in db.execute("SELECT payload FROM personas")
        )


def test_demo_refuses_to_seed_an_existing_non_demo_database(tmp_path):
    data = tmp_path / "existing"
    data.mkdir()
    database = data / "backend.sqlite3"
    database.write_bytes(b"existing-backend-marker")
    result = run_demo(tmp_path, data)
    assert result.returncode != 0
    assert "existing backend" in result.stderr
    assert database.read_bytes() == b"existing-backend-marker"
    assert not (data / "portable-demo.json").exists()
