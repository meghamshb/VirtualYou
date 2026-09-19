import asyncio
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from virtual_you.backend.collection import Collector
from virtual_you.backend.drafting import DraftEngine
from virtual_you.backend.errors import ServiceError
from virtual_you.backend.providers import UNKNOWN
from virtual_you.backend.retrieval import RetrievalService
from virtual_you.backend.store import Store
from virtual_you.contracts.reporting import RetrievalRequest


def test_collector_to_rag_preserves_history_redacts_and_is_incremental(settings, tmp_path):
    settings.prepare()
    raw = tmp_path / "sessions"
    raw.mkdir()
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / ".env").write_text("API_KEY=orchard-private-value\n")
    source = raw / "session.jsonl"
    fixture = Path(__file__).parents[1] / "fixtures/claude_session.jsonl"
    source.write_text(fixture.read_text().replace("payment", "orchard-private-value"))
    config = tmp_path / "sources.json"
    config.write_text(
        json.dumps(
            [
                {
                    "source": "claude",
                    "path": str(raw),
                    "workspace": str(workspace),
                    "project": "virtualyou",
                }
            ]
        )
    )
    settings.ingestion_config = config
    collector = Collector(settings)
    assert collector.collect()["changed"] == 1
    assert collector.collect()["changed"] == 0
    files = list(settings.activity_dir.rglob("activity-*.json"))
    assert len(files) == 1
    assert "orchard-private-value" not in files[0].read_text()
    store = Store(settings.data_dir / "test.sqlite")
    retrieval = RetrievalService(store)
    record = json.loads(files[0].read_text())
    retrieval.upsert(record)
    retrieval.assign_project([record["session_id"]], "virtualyou")
    assert retrieval.evidence(RetrievalRequest(project_ids=["virtualyou"]))
    assert not retrieval.evidence(RetrievalRequest(project_ids=["another"]))
    # Same native session ID in another configured project cannot overwrite it.
    config.write_text(
        json.dumps(
            [
                {
                    "source": "claude",
                    "path": str(raw),
                    "workspace": str(workspace),
                    "project": "another",
                }
            ]
        )
    )
    assert collector.collect()["changed"] == 1
    assert (
        len(
            {
                json.loads(p.read_text())["session_id"]
                for p in settings.activity_dir.rglob("activity-*.json")
            }
        )
        == 2
    )


def test_long_patch_tail_search_and_snapshot_invalidation(settings, record):
    settings.prepare()
    retrieval = RetrievalService(Store(settings.data_dir / "test.sqlite"))
    record["diffs"] = ["x " * 10000 + "\n+ fix_zebracorn_race_condition()"]
    retrieval.upsert(record)
    retrieval.assign_project([record["session_id"]], "virtualyou")
    scope = RetrievalRequest(query="fix_zebracorn_race_condition", project_ids=["virtualyou"])
    evidence = retrieval.evidence(scope)
    assert any("fix_zebracorn_race_condition" in e.text for e in evidence)
    assert sum(len(e.text) for e in evidence) <= 18000
    saved = [e.model_dump() for e in evidence]
    retrieval.validate_snapshot(saved, scope)
    retrieval.assign_project([record["session_id"]], "private")
    with pytest.raises(ServiceError, match="audience"):
        retrieval.validate_snapshot(saved, scope)


def test_llm_search_refinement_cannot_widen_projects(settings, record):
    settings.prepare()
    store = Store(settings.data_dir / "test.sqlite")
    retrieval = RetrievalService(store)
    record["end_state"] = "The callback validator now rejects invalid input."
    retrieval.upsert(record)
    retrieval.assign_project([record["session_id"]], "allowed")
    calls = []

    class Model:
        async def generate(self, **kwargs):
            data = json.loads(kwargs["user"])
            calls.append(data)
            if len(calls) == 1:
                return {
                    "paragraphs": [{"text": UNKNOWN, "citations": []}],
                    "search_query": "callback validator",
                }
            source = next(e for e in data["evidence"] if e["field"] == "end_state")
            return {
                "paragraphs": [
                    {
                        "text": source["text"],
                        "citations": [
                            {"evidence_id": source["evidence_id"], "quote": source["text"]}
                        ],
                    }
                ]
            }

    engine = DraftEngine(settings, store, retrieval, Model())
    reply = asyncio.run(
        engine.reply(
            question="What about the webhook?",
            scope=RetrievalRequest(project_ids=["allowed"]),
            style={"tone": "terse"},
        )
    )
    assert reply["model_calls"] == 2
    assert calls[1]["style_only"] == {"tone": "terse"}
    assert "rejects invalid" in reply["text"]
    # No authorized project means no source data, even for the refinement.
    calls.clear()

    class Denied:
        async def generate(self, **kwargs):
            assert not json.loads(kwargs["user"])["evidence"]
            return {"paragraphs": [{"text": UNKNOWN, "citations": []}], "search_query": "callback"}

    engine.provider = Denied()
    reply = asyncio.run(
        engine.reply(question="webhook", scope=RetrievalRequest(project_ids=[]), style={})
    )
    assert reply["text"] == UNKNOWN and reply["model_calls"] == 2


def test_unsupported_model_claim_fails_before_approval(settings):
    settings.prepare()
    store = Store(settings.data_dir / "test.sqlite")

    class Model:
        async def generate(self, **kwargs):
            return {"paragraphs": [{"text": "All tests passed and deployed.", "citations": []}]}

    engine = DraftEngine(settings, store, RetrievalService(store), Model())
    with pytest.raises(ServiceError) as error:
        asyncio.run(engine.reply(question="status", scope=RetrievalRequest(), style={}))
    assert error.value.code == "unsupported_claim"


def test_conversational_reply_uses_delivery_references_for_change_context(settings, record):
    settings.prepare()
    store = Store(settings.data_dir / "test.sqlite")
    retrieval = RetrievalService(store)
    record["end_state"] = "The GitHub connector is now connected to Slack."
    retrieval.upsert(record)
    captured = {}

    class Model:
        async def generate(self, **kwargs):
            data = json.loads(kwargs["user"])
            captured.update(data)
            source = next(item for item in data["evidence"] if item["field"] == "end_state")
            return {
                "paragraphs": [
                    {
                        "text": source["text"],
                        "citations": [{"evidence_id": source["evidence_id"]}],
                    }
                ]
            }

    current = next(
        item for item in retrieval.evidence(RetrievalRequest()) if item.field == "end_state"
    )
    earlier_version = current.model_dump()
    earlier_version["record_hash"] = "previous-version"
    earlier_version["text_hash"] = hashlib.sha256(b"Earlier connector state.").hexdigest()
    result = asyncio.run(
        DraftEngine(settings, store, retrieval, Model()).reply(
            question="What changed since your last update?",
            scope=RetrievalRequest(),
            style={"tone": "concise"},
            conversation_history=[{"role": "owner", "text": "I sent the earlier update."}],
            delivered_evidence_refs=[earlier_version],
            has_prior_delivery=True,
        )
    )

    assert captured["conversation_history"] == [
        {"role": "owner", "text": "I sent the earlier update."}
    ]
    assert captured["delivery_baseline"] == {
        "has_prior_delivery": True,
        "delivered_evidence_count": 1,
    }
    source = next(item for item in captured["evidence"] if item["field"] == "end_state")
    assert source["delivery_status"] == "changed_since_delivery"
    assert result["paragraphs"][0]["citations"][0]["evidence_id"] == source["evidence_id"]


def test_real_git_commit_becomes_redacted_report_evidence(settings, tmp_path):
    from virtual_you.ingest.git import collect_commits

    root = tmp_path / "repo"
    root.mkdir()

    def git(*args):
        return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (root / "change.py").write_text("def safer(): return True\n")
    (root / ".env").write_text("API_KEY=orchard-private-value\n")
    git("add", "change.py", ".env")
    git("commit", "-m", "Guard missing input before processing")
    records = collect_commits(root)
    assert len(records) == 1 and records[0].source == "git"
    assert "+def safer()" in records[0].diffs[0]
    assert "orchard-private-value" not in records[0].model_dump_json()
    assert ".env" not in records[0].diffs[0]
    assert "not verified" in records[0].end_state


def test_git_history_keeps_distinct_ids_through_redaction(tmp_path):
    from virtual_you.ingest.git import collect_commits

    root = tmp_path / "repo"
    root.mkdir()

    def git(*args):
        return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    for i in range(8):
        (root / "change.py").write_text(f"version = {i}\n")
        git("add", "change.py")
        git("commit", "-m", f"Change {i}")
    records = collect_commits(root)
    assert len(records) == len({r.session_id for r in records}) == 8
    assert all("REDACTED" not in r.session_id for r in records)


def test_invalid_source_gets_one_repair_and_never_bypasses_validation(settings, record):
    settings.prepare()
    store = Store(settings.data_dir / "repair.sqlite")
    retrieval = RetrievalService(store)
    retrieval.upsert(record)
    calls = []

    class Model:
        async def generate(self, **kwargs):
            data = json.loads(kwargs["user"])
            calls.append(data)
            source = data["evidence"][0]
            return {
                "paragraphs": [
                    {
                        "text": source["text"],
                        "citations": [
                            {
                                "evidence_id": "missing-source"
                                if len(calls) == 1
                                else source["evidence_id"],
                                "quote": "fabricated quotation"
                                if len(calls) == 1
                                else source["text"][:100],
                            }
                        ],
                    }
                ]
            }

    result = asyncio.run(
        DraftEngine(settings, store, retrieval, Model()).reply(
            question="status", scope=RetrievalRequest(), style={}
        )
    )
    assert result["model_calls"] == 2
    assert "invalid_citation" in calls[1]["validation_feedback"]


def test_codex_collector_publishes_bounded_chunks_and_skips_unchanged(settings, tmp_path):
    settings.prepare()
    source = tmp_path / "session.jsonl"
    rows = [
        {"type": "session_meta", "payload": {"id": "test-codex"}},
        {
            "type": "response_item",
            "payload": {"type": "message", "role": "user", "content": "trial-test 1.0"},
        },
        {
            "type": "event_msg",
            "payload": {"type": "task_complete", "last_agent_message": "Public trial result"},
        },
        {"type": "event_msg", "payload": {"type": "task_started"}},
        {
            "type": "response_item",
            "payload": {"type": "message", "role": "user", "content": "Next question"},
        },
    ]
    source.write_text("".join(json.dumps(row) + "\n" for row in rows))
    config = tmp_path / "sources.json"
    config.write_text(
        json.dumps(
            [
                {
                    "source": "codex",
                    "path": str(source),
                    "workspace": str(tmp_path),
                    "project": "virtualyou",
                }
            ]
        )
    )
    settings.ingestion_config = config
    collector = Collector(settings)
    first = collector.collect()
    assert first["changed"] == 2 and first["errors"] == []
    assert collector.collect()["changed"] == 0
    records = [json.loads(p.read_text()) for p in settings.activity_dir.rglob("activity-*.json")]
    assert len({r["session_id"] for r in records}) == 2
    assert all(r["source"] == "codex" and r["redacted"] for r in records)
