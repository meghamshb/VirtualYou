"""A commit reference is presentation, not a reason to discard a valid DM reply."""

import asyncio
import json
import time

from virtual_you.contracts.reporting import RetrievalRequest, utcnow

from virtualyou_workflow.formatting import ASSISTED_REPLY_LABEL, slack_text

from .test_dm_replies import make_monitor


def test_latest_commit_with_full_sha_and_link_reaches_approval_then_exact_delivery(tmp_path):
    monitor, sends = make_monitor(tmp_path)
    sha = "abcdef1234" * 4
    url = "https://github.com/team/work/commit/" + sha
    stamp = utcnow()
    source_text = "Recorded Git commit " + sha + ": Fixed callback validation. " + url
    monitor.c.backend.retrieval.upsert({
        "session_id": "latest-commit", "source": "git", "redacted": True,
        "end_state": source_text,
        "timestamp_range": {"started_at": stamp, "ended_at": stamp},
    })
    monitor.c.backend.retrieval.assign_project(["latest-commit"], "A")
    monitor.c.scope = lambda person: RetrievalRequest(project_ids=person["projects"], sources=["git"])
    generations = []

    class Model:
        async def generate(self, **kwargs):
            prompt = json.loads(kwargs["user"])
            generations.append(prompt)
            evidence = next(e for e in prompt["evidence"] if e["field"] == "end_state")
            return {"paragraphs": [{
                "text": "Commit " + sha + " fixed callback validation. " + url,
                "citations": [{"evidence_id": evidence["evidence_id"]}],
            }]}

    monitor.c.backend.engine.provider = Model()

    async def run():
        monitor.receive_event({
            "channel": "DHUMAN", "channel_type": "im", "user": "UFRIEND",
            "text": "What is the latest commit?", "ts": f"{time.time() + 1:.6f}",
        }, "TTEAM")
        await monitor.prepare_one()
        with monitor.c.backend.store.connection() as db:
            row = dict(db.execute("SELECT * FROM slack_dm_replies").fetchone())
        assert row["state"] == "pending"
        assert monitor.c.backend.assistant.get(row["id"])["status"] == "draft_ready"
        assert len(generations) == 1
        expected = ASSISTED_REPLY_LABEL + "\n\nCommit " + sha[:7] + " fixed callback validation. " + url
        assert row["reply"] == expected
        grounding = json.loads(row["grounding"])
        assert grounding["paragraphs"][0]["citations"][0]["quote"] == source_text
        assert len(sends) == 1 and sends[0]["channel"] == "DBOTUOWNER"
        # The owner has only an approval card; the colleague gets nothing yet.
        await monitor.decide(row["id"], True)
        await monitor.decide(row["id"], True)
        outbound = [payload for payload in sends if payload["channel"] == "DHUMAN"]
        assert len(outbound) == 1
        assert outbound[0]["text"] == slack_text(expected)
        assert monitor.get(row["id"])["reply"] == expected
        assert monitor.get(row["id"])["state"] == "sent"

    asyncio.run(run())
