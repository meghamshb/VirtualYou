"""JSONL observation log with delivery and (kind, sha, id) dedup."""

import json
from pathlib import Path
from typing import List, Optional, Sequence, Union

from virtual_you.mcp.work_state import Observation

PathLike = Union[str, Path]
OBSERVATIONS_NAME = "github-observations.jsonl"


class ObservationStore:
    def __init__(self, directory: PathLike) -> None:
        self.path = Path(directory) / OBSERVATIONS_NAME

    def append(self, observation: Observation) -> bool:
        if self.contains(observation):
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_to_dict(observation), sort_keys=True) + "\n")
        return True

    def extend(self, observations: Sequence[Observation]) -> int:
        written = 0
        for item in observations:
            written += int(self.append(item))
        return written

    def all(self) -> List[Observation]:
        if not self.path.exists():
            return []
        items: List[Observation] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            items.append(_from_dict(payload))
        return items

    def contains(self, observation: Observation) -> bool:
        for existing in self.all():
            if observation.event_id and existing.event_id == observation.event_id:
                return True
            if (
                existing.kind == observation.kind
                and existing.sha == observation.sha
                and existing.event_id
                and existing.event_id == observation.event_id
            ):
                return True
            if (
                not observation.event_id
                and existing.kind == observation.kind
                and existing.sha == observation.sha
                and existing.detail == observation.detail
            ):
                return True
        return False

    def known_shas(self) -> List[str]:
        seen = []
        for item in self.all():
            if item.sha and item.sha not in seen:
                seen.append(item.sha)
        return seen


def _to_dict(observation: Observation) -> dict:
    return {
        "source": observation.source,
        "timestamp": observation.timestamp,
        "sha": observation.sha,
        "task_id": observation.task_id,
        "kind": observation.kind,
        "status": observation.status,
        "detail": observation.detail,
        "url": observation.url,
        "event_id": observation.event_id,
        "extra": observation.extra,
    }


def _from_dict(payload: dict) -> Observation:
    return Observation(
        source=str(payload.get("source") or ""),
        timestamp=str(payload.get("timestamp") or ""),
        sha=str(payload.get("sha") or ""),
        task_id=str(payload.get("task_id") or ""),
        kind=str(payload.get("kind") or ""),
        status=str(payload.get("status") or ""),
        detail=str(payload.get("detail") or ""),
        url=str(payload.get("url") or ""),
        event_id=str(payload.get("event_id") or ""),
        extra=dict(payload.get("extra") or {}),
    )
