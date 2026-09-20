"""Small, owner-only activity projections; never expose raw logs or configuration."""

from __future__ import annotations

import json
import os
import re
from pathlib import PurePosixPath, PureWindowsPath

from pydantic import BaseModel, ConfigDict, StrictBool

from virtual_you.backend.errors import ServiceError
from virtual_you.contracts.activity import ActivityRecord, SourceKind
from virtual_you.ingest.redact import redact_text, redact_value
from virtual_you.mcp.drive import RestDriveClient, drive_enabled
from virtual_you.mcp.enrich import github_enabled
from virtual_you.mcp.jira import RestJiraClient, jira_enabled
from virtual_you.mcp.oauth import load_token

ERROR_MESSAGES = {
    "invalid_ingestion_config": "Check the configured source list; it could not be read or validated.",
    "source_unavailable": "Check that the selected source and workspace still exist and are readable.",
    "source_ingestion_failed": "A selected source could not be parsed. Check its format and access permissions.",
    "no_source_files": "No matching files were found in a selected source directory.",
    "directory_unavailable": "The normalized activity directory is unavailable. Check local access permissions.",
    "invalid_activity_record": "A normalized activity record failed validation and was excluded.",
    "feed_refresh_failed": "The activity feed could not be refreshed. Check its configuration and availability.",
    "collection_failed": "Source collection failed. Check the local source setup and try refreshing again.",
    "refresh_failed": "Activity refresh failed. Try refreshing again and check the local source setup.",
    "github_scope_conflict": "A project has conflicting GitHub repositories. Check its source configuration.",
    "github_scope_unavailable": "Link a source project to its GitHub repository before refreshing GitHub evidence.",
    "github_not_configured": "GitHub refresh is enabled but repository credentials are unavailable.",
    "github_lookup_failed": "GitHub evidence could not be verified. Check repository access, API limits, and connectivity.",
    "jira_not_configured": "Jira refresh is enabled but its local credentials are incomplete.",
    "jira_lookup_failed": "A named Jira issue could not be verified. Check the issue key, access, and connectivity.",
}
ACTION_TITLES = {
    "created": "Draft created",
    "edited": "Draft edited",
    "regenerated": "Draft regenerated",
    "approve": "Draft approved",
    "reject": "Draft rejected",
    "delivery_started": "Delivery started",
    "delivered": "Draft delivered",
    "simulated": "Delivery simulated",
    "delivery_failed": "Delivery failed",
    "delivery_unknown": "Delivery needs reconciliation",
    "reconciled_confirmed_delivered": "Delivery confirmed",
    "reconciled_confirmed_not_delivered": "Delivery marked not sent",
}


class WorkflowPause(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paused: StrictBool


def _workflow_available(app_state):
    worker = getattr(app_state, "slack_worker", None)
    return getattr(app_state, "slack_coordinator", None) is not None and not (
        worker is not None and worker.done()
    )


def workflow_summary(store, app_state):
    return {
        "available": _workflow_available(app_state),
        "paused": (store.metadata("slack_preferences") or {}).get("paused") is True,
    }


def set_workflow_pause(store, app_state, paused):
    if not _workflow_available(app_state):
        raise ServiceError(
            "workflow_unavailable", "The Slack workflow is not running on this backend.", 409
        )
    with store.connection(write=True) as db:
        row = db.execute("SELECT payload FROM metadata WHERE key='slack_preferences'").fetchone()
        preferences = json.loads(row[0]) if row else {"sources": []}
        preferences["paused"] = paused
        db.execute(
            "INSERT OR REPLACE INTO metadata VALUES (?,?)",
            ("slack_preferences", json.dumps(preferences)),
        )
    return {"available": True, "paused": paused}


def _text(value, limit=160):
    return _context_text(value)[:limit]


def _context_text(value):
    """Hide private filesystem locations while retaining API routes and evidence URLs."""
    text = redact_text(str(value or ""))
    local_prefix = (
        r"/(?:Users|home|private|tmp|var|opt|usr|etc|Library|Applications|System|Volumes|"
        r"root|workspace|workspaces|mnt|srv|run|dev|proc|sys|media)(?=/|\b)"
        r"|[A-Za-z]:[\\/]|\\\\|file:///"
    )

    def basename(path):
        return "[local]/" + re.split(r"[/\\]", path.rstrip("/\\"))[-1]

    def hide(part):
        # First preserve quoted filenames containing spaces as a single path.
        part = re.sub(
            r"([\"'])((?:" + local_prefix + r")[^\"'\n]*)\1",
            lambda match: match[1] + basename(match[2]) + match[1], part,
        )
        return re.sub(
            r"(?<![\w:/\\])(?:" + local_prefix + r")([^\s<>\"'`|()\[\]{}]*)",
            lambda match: basename(match[0]), part,
        )

    pieces, start = [], 0
    for url in re.finditer(r"https?://[^\s<>\"'`|()]+", text):
        pieces.extend((hide(text[start:url.start()]), url[0]))
        start = url.end()
    return "".join([*pieces, hide(text[start:])])


def collection_summary(settings, store, retrieval):
    heartbeat = store.metadata("heartbeat") or {}
    collected = heartbeat.get("collection") or {}
    count = retrieval.stats()["count"]
    errors = []
    allowed_sources = {item.value for item in SourceKind} | {"local", "feed", "collector", "github", "jira"}
    for error in heartbeat.get("errors", [])[:20]:
        code = error.get("code")
        code = code if code in ERROR_MESSAGES else "refresh_failed"
        source = error.get("source")
        errors.append(
            {
                "source": source if source in allowed_sources else "collector",
                "code": code,
                "message": ERROR_MESSAGES[code],
            }
        )
    state = heartbeat.get("state")
    if state not in {"healthy", "degraded", "failed"}:
        state = "not_started"
    if errors and state not in {"degraded", "failed"}:
        state = "degraded"
    elif state == "healthy" and not count:
        state = "empty"
    return {
        "state": state,
        "enabled": settings.heartbeat_enabled,
        "configured": bool(collected.get("configured_sources", 0) or settings.activity_feed_url),
        "last_attempt_at": heartbeat.get("started_at") or heartbeat.get("finished_at"),
        "last_success_at": heartbeat.get("last_success_at"),
        "record_count": count,
        "changed": heartbeat.get("changed", 0),
        "unchanged": heartbeat.get("unchanged", 0),
        "removed": heartbeat.get("removed", 0),
        "error_count": max(heartbeat.get("error_count", 0), len(errors)),
        "errors": errors,
        "configured_sources": collected.get("configured_sources", 0),
        "empty_inputs": collected.get("empty", 0),
    }


def integration_summary(settings, app_state):
    """Read local configuration only. Credential presence is not live acceptance."""

    def entry(configured, enabled=True, **extra):
        return {
            "status": "configured" if configured else "not_configured",
            "enabled": enabled,
            "verification": "not_checked",
            **extra,
        }

    coordinator = getattr(app_state, "slack_coordinator", None)
    installed = False
    slack_configured = bool(settings.slack_bot_token)
    if coordinator is not None:
        # Reuse the owner/workspace-scoped installation resolver. Never scan token files.
        try:
            installation = coordinator.credentials.installation()
            installed = bool(installation and installation.user_token and installation.bot_token)
            slack_configured = bool(
                coordinator.credentials.user_token() and coordinator.credentials.bot_token()
            )
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            slack_configured = False
    try:
        github_token = bool(os.getenv("GITHUB_TOKEN") or load_token(settings.data_dir))
    except (OSError, ValueError, TypeError, AttributeError):
        github_token = False
    return {
        "slack": entry(
            slack_configured,
            enabled=coordinator is not None or bool(settings.slack_bot_token),
            oauth_installed=installed,
            oauth_configured=all(
                os.getenv(name)
                for name in ("SLACK_CLIENT_ID", "SLACK_CLIENT_SECRET", "SLACK_REDIRECT_URI")
            ),
        ),
        "github": entry(
            bool(os.getenv("VIRTUAL_YOU_GITHUB_REPO") and github_token),
            enabled=github_enabled(),
        ),
        "jira": entry(RestJiraClient.from_env(os.environ) is not None, enabled=jira_enabled()),
        "drive": entry(RestDriveClient.from_env(os.environ) is not None, enabled=drive_enabled()),
    }


def activity_feed(settings, store, retrieval, limit):
    with store.connection() as db:
        rows = db.execute(
            """SELECT 'activity' AS kind, a.id AS row_id, a.ended_at AS at,
                      a.payload, p.project_id, NULL AS draft_id, NULL AS revision,
                      NULL AS action, NULL AS status
               FROM activities a LEFT JOIN activity_projects p ON p.session_id=a.session_id
               UNION ALL
               SELECT 'draft_event', a.id, a.at, d.payload, NULL, a.draft_id,
                      a.revision, a.action, d.status
               FROM audit a JOIN drafts d ON d.id=a.draft_id
               ORDER BY at DESC, kind, row_id DESC LIMIT ?""",
            (limit + 1,),
        ).fetchall()
    items = []
    for row in rows[:limit]:
        payload = json.loads(row["payload"])
        if row["kind"] == "activity":
            record = ActivityRecord.model_validate(redact_value(payload))
            summary = (
                record.end_state
                or record.start_state
                or (
                    f"{len(record.files_changed)} file changes and {len(record.tool_calls)} tool events recorded."
                )
            )
            items.append(
                {
                    "id": f"activity:{row['row_id']}",
                    "kind": "activity",
                    "at": row["at"],
                    "title": f"{record.source.title()} activity",
                    "summary": _text(summary, 600),
                    "source": record.source,
                    "project_id": _text(row["project_id"], 80) if row["project_id"] else None,
                    "session_id": _text(record.session_id),
                    "files_changed_count": len(record.files_changed),
                    "tool_calls_count": len(record.tool_calls),
                }
            )
        else:
            destination = payload.get("destination") or {}
            action = row["action"] if row["action"] in ACTION_TITLES else "updated"
            items.append(
                {
                    "id": f"draft-event:{row['row_id']}",
                    "kind": "draft_event",
                    "at": row["at"],
                    "title": ACTION_TITLES.get(action, "Draft updated"),
                    "summary": f"Revision {row['revision']} · {action.replace('_', ' ')}",
                    "draft_id": _text(row["draft_id"]),
                    "revision": row["revision"],
                    "action": action,
                    "status": _text(row["status"], 40),
                    "recipient_id": _text((payload.get("request") or {}).get("recipient_id")),
                    "destination": {
                        "platform": _text(destination.get("platform"), 40),
                        "target": _text(destination.get("target")),
                    },
                }
            )
    return {
        "items": items,
        "limit": limit,
        "has_more": len(rows) > limit,
        "collection": collection_summary(settings, store, retrieval),
        "source_counts": source_counts(store),
    }


def source_counts(store):
    """Count indexed source records separately from configured collectors and model use."""
    with store.connection() as db:
        counts = {
            row["source"]: row
            for row in db.execute(
                """SELECT json_extract(payload, '$.source') AS source,
                          COUNT(*) AS count, MAX(ended_at) AS latest_at
                   FROM activities GROUP BY json_extract(payload, '$.source')"""
            ).fetchall()
        }
    heartbeat = store.metadata("heartbeat") or {}
    configured = {
        item["source"]: item
        for item in (heartbeat.get("collection") or {}).get("sources", [])
        if isinstance(item, dict) and item.get("source") in {source.value for source in SourceKind}
    }
    rows = []
    for source in SourceKind:
        count = counts.get(source.value)
        collection = configured.get(source.value, {})
        state = collection.get("scan_state", "not_started")
        if state not in {"healthy", "empty", "failed", "not_started"}:
            state = "not_started"
        if collection and heartbeat.get("state") == "failed":
            state = "failed"
        elif state == "healthy" and not count:
            state = "empty"
        rows.append({
            "source": source.value,
            "count": count["count"] if count else 0,
            "latest_at": count["latest_at"] if count else None,
            "configured": collection.get("configured") is True,
            "scan_state": state,
            "last_scan_at": heartbeat.get("finished_at") if collection else None,
        })
    return rows


class DetailProjection:
    """Bound the owner-facing excerpt after redaction, including a total text budget."""

    def __init__(self):
        self.remaining = 24_000
        self.truncated = False

    def text(self, value, limit):
        text = _context_text(value)
        size = min(limit, self.remaining)
        self.truncated |= len(text) > size
        result = text[:size]
        self.remaining -= len(result)
        return result

    def take(self, items, limit):
        self.truncated |= len(items) > limit
        return items[:limit]

    def path(self, path):
        # Absolute changed-file paths are not raw source provenance. Expose a basename only.
        if PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute():
            path = "[local]/" + re.split(r"[/\\]", path)[-1]
        return self.text(path, 300)


def activity_detail(store, activity_id):
    # Route with the opaque feed row ID; session identifiers can contain private paths.
    match = re.fullmatch(r"activity:([1-9][0-9]{0,17})", activity_id)
    if not match:
        raise ServiceError("activity_not_found", "This activity record is unavailable.", 404)
    with store.connection() as db:
        row = db.execute(
            """SELECT a.payload, a.indexed_at, p.project_id
               FROM activities a LEFT JOIN activity_projects p ON p.session_id=a.session_id
               WHERE a.id=?""", (int(match[1]),),
        ).fetchone()
    if not row:
        raise ServiceError("activity_not_found", "This activity record is unavailable.", 404)
    record = ActivityRecord.model_validate(redact_value(json.loads(row["payload"])))
    view = DetailProjection()
    detail = {
        "id": activity_id,
        "session_id": view.text(record.session_id, 160),
        "source": record.source,
        "project_id": view.text(row["project_id"], 80) if row["project_id"] else None,
        "started_at": record.timestamp_range.started_at.isoformat(),
        "ended_at": record.timestamp_range.ended_at.isoformat(),
        "ingested_at": row["indexed_at"],
        "summary": view.text(record.end_state or record.start_state, 1200),
        "start_state": view.text(record.start_state, 2000),
        "end_state": view.text(record.end_state, 3000),
        "prompts": [view.text(prompt, 1500) for prompt in view.take(record.prompts, 8)],
        "reasoning_summary": view.text(record.reasoning_summary, 2000),
        "files_changed": [
            {"path": view.path(item.path), "operation": item.operation.value}
            for item in view.take(record.files_changed, 40)
        ],
        "tool_calls": [
            {
                "name": view.text(call.name, 120),
                "input_summary": view.text(call.input_summary, 400),
                "result_summary": view.text(call.result_summary, 1200),
                "status": call.status,
                "timestamp": call.timestamp.isoformat() if call.timestamp else None,
            }
            for call in view.take(record.tool_calls, 20)
        ],
        "diffs": [view.text(diff, 2000) for diff in view.take(record.diffs, 5)],
        "redacted": True,
    }
    detail["truncated"] = view.truncated
    return detail
