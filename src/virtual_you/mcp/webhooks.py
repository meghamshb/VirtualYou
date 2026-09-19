"""Verify GitHub webhook signatures and map events to observations."""

import hashlib
import hmac
import json
from typing import List

from virtual_you.mcp.observations import ObservationStore
from virtual_you.mcp.work_state import Observation

SIGNATURE_HEADER = "X-Hub-Signature-256"
DELIVERY_HEADER = "X-GitHub-Delivery"
EVENT_HEADER = "X-GitHub-Event"


def verify_signature(secret: str, body: bytes, header: str) -> bool:
    if not secret or not header:
        return False
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    expected = "sha256=" + digest
    return hmac.compare_digest(expected, header)


def apply_webhook(
    store: ObservationStore,
    *,
    event: str,
    delivery_id: str,
    payload: dict,
) -> int:
    observations = observations_from_event(event, delivery_id, payload)
    return store.extend(observations)


def observations_from_event(
    event: str,
    delivery_id: str,
    payload: dict,
) -> List[Observation]:
    if event in ("push",):
        return _from_push(delivery_id, payload)
    if event == "pull_request":
        return _from_pull_request(delivery_id, payload)
    if event == "pull_request_review":
        return _from_review(delivery_id, payload)
    if event in ("issues", "issue_comment"):
        return _from_issue(delivery_id, payload)
    if event in ("check_run", "check_suite"):
        return _from_check(delivery_id, payload)
    return []


def decode_payload(body: bytes) -> dict:
    return json.loads(body.decode("utf-8"))


def _from_push(delivery_id: str, payload: dict) -> List[Observation]:
    head = str(payload.get("after") or payload.get("head_commit", {}).get("id") or "")
    if not head or head == "0" * 40:
        return []
    url = str((payload.get("head_commit") or {}).get("url") or "")
    timestamp = str((payload.get("head_commit") or {}).get("timestamp") or "")
    return [
        Observation(
            source="github.commit",
            timestamp=timestamp,
            sha=head,
            task_id=head,
            kind="committed",
            status="verified",
            detail="sha {} committed".format(head),
            url=url,
            event_id="delivery:{}".format(delivery_id) if delivery_id else "push:{}".format(head),
        )
    ]


def _from_pull_request(delivery_id: str, payload: dict) -> List[Observation]:
    pull = payload.get("pull_request") or {}
    sha = str((pull.get("head") or {}).get("sha") or "")
    number = int(pull.get("number") or 0)
    merged = bool(pull.get("merged"))
    url = str(pull.get("html_url") or "")
    action = str(payload.get("action") or "")
    kind = "merged" if merged or action == "closed" and pull.get("merged_at") else "pr"
    return [
        Observation(
            source="github.pr",
            timestamp=str(pull.get("updated_at") or ""),
            sha=sha,
            task_id=str(number),
            kind=kind,
            status="verified",
            detail="{} {} #{} {}".format(url, "merged" if kind == "merged" else pull.get("state"), number, sha),
            url=url,
            event_id="delivery:{}".format(delivery_id) if delivery_id else "pr:{}".format(number),
            extra={"merged": kind == "merged", "number": number, "action": action},
        )
    ]


def _from_review(delivery_id: str, payload: dict) -> List[Observation]:
    review = payload.get("review") or {}
    pull = payload.get("pull_request") or {}
    sha = str(review.get("commit_id") or (pull.get("head") or {}).get("sha") or "")
    state = str(review.get("state") or "")
    submitted = str(review.get("submitted_at") or "")
    review_id = str(review.get("id") or "")
    return [
        Observation(
            source="github.review",
            timestamp=submitted,
            sha=sha,
            task_id=str(pull.get("number") or ""),
            kind="review",
            status="verified",
            detail="sha {} review {} submitted {}".format(sha, state.upper().replace(" ", "_"), submitted),
            url=str(review.get("html_url") or ""),
            event_id="delivery:{}".format(delivery_id) if delivery_id else "review:{}".format(review_id),
            extra={"state": state.upper().replace(" ", "_"), "submitted_at": submitted},
        )
    ]


def _from_issue(delivery_id: str, payload: dict) -> List[Observation]:
    issue = payload.get("issue") or {}
    number = int(issue.get("number") or 0)
    sha = ""
    return [
        Observation(
            source="github.issue",
            timestamp=str(issue.get("updated_at") or ""),
            sha=sha,
            task_id=str(number),
            kind="issue",
            status="verified",
            detail="{} {} {}".format(issue.get("html_url") or "", issue.get("state"), issue.get("title")),
            url=str(issue.get("html_url") or ""),
            event_id="delivery:{}".format(delivery_id) if delivery_id else "issue:{}".format(number),
        )
    ]


def _from_check(delivery_id: str, payload: dict) -> List[Observation]:
    run = payload.get("check_run") or {}
    suite = payload.get("check_suite") or {}
    sha = str(run.get("head_sha") or suite.get("head_sha") or "")
    conclusion = str(run.get("conclusion") or suite.get("conclusion") or "")
    url = str(run.get("html_url") or "")
    name = str(run.get("name") or "check")
    check_id = str(run.get("id") or suite.get("id") or "")
    if not sha:
        return []
    return [
        Observation(
            source="github.checks",
            timestamp=str(run.get("completed_at") or suite.get("updated_at") or ""),
            sha=sha,
            task_id=sha,
            kind="checks",
            status="verified",
            detail="sha {} checks {} {}".format(sha, conclusion, url).strip(),
            url=url,
            event_id="delivery:{}".format(delivery_id) if delivery_id else "check:{}".format(check_id),
            extra={"conclusion": conclusion, "name": name},
        )
    ]


def header_value(headers: dict, name: str) -> str:
    lower = name.lower()
    for key, value in headers.items():
        if str(key).lower() == lower:
            return str(value or "")
    return ""
