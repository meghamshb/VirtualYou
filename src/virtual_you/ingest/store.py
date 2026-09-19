"""Local JSON repository for sanitized activity records."""

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Union

from virtual_you.contracts.activity import ActivityRecord


PathLike = Union[str, os.PathLike[str]]
_UNSAFE_FILENAME_CHARACTERS = re.compile(r"[^A-Za-z0-9._-]+")


class ActivityRecordRepository:
    """Store only redacted ``ActivityRecord`` objects as individual JSON files."""

    def __init__(self, directory: PathLike) -> None:
        self.directory = Path(directory)

    def save(
        self, record: Union[ActivityRecord, Mapping[str, Any]]
    ) -> ActivityRecord:
        raw_redacted = (
            record.redacted
            if isinstance(record, ActivityRecord)
            else record.get("redacted")
        )
        if raw_redacted is not True:
            raise ValueError("refusing to store an activity record that is not redacted")

        validated = (
            record
            if isinstance(record, ActivityRecord)
            else ActivityRecord.model_validate(record)
        )
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        destination = self._path_for(validated.session_id)
        temporary = destination.with_name(destination.name + ".tmp")
        temporary.write_text(
            json.dumps(
                validated.model_dump(mode="json"),
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(destination)
        return validated

    def get(self, session_id: str) -> Optional[ActivityRecord]:
        path = self._path_for(session_id)
        if not path.exists():
            return None
        record = self._load(path)
        if record.session_id != session_id:
            return None
        return record

    def all(self) -> List[ActivityRecord]:
        if not self.directory.exists():
            return []
        records = [
            self._load(path)
            for path in sorted(self.directory.glob("activity-*.json"))
            if path.is_file()
        ]
        return sorted(records, key=self._sort_key)

    def latest_activity(self) -> Optional[ActivityRecord]:
        records = self.all()
        return records[-1] if records else None

    def export(self, destination: Optional[PathLike] = None) -> str:
        """Return a deterministic JSON export and optionally write it to disk."""

        payload = [record.model_dump(mode="json") for record in self.all()]
        serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        if destination is not None:
            export_path = Path(destination)
            export_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = export_path.with_name(export_path.name + ".tmp")
            temporary.write_text(serialized, encoding="utf-8")
            temporary.chmod(0o600)
            temporary.replace(export_path)
        return serialized

    def _path_for(self, session_id: str) -> Path:
        slug = _UNSAFE_FILENAME_CHARACTERS.sub("-", session_id).strip(".-_")
        if not slug:
            slug = "session"
        slug = slug[:80]
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]
        return self.directory / "activity-{}-{}.json".format(slug, digest)

    @staticmethod
    def _load(path: Path) -> ActivityRecord:
        payload: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("redacted") is not True:
            raise ValueError("stored activity record is not marked as redacted")
        return ActivityRecord.model_validate(payload)

    @staticmethod
    def _sort_key(record: ActivityRecord) -> tuple:
        ended_at = _as_utc(record.timestamp_range.ended_at)
        started_at = _as_utc(record.timestamp_range.started_at)
        return ended_at, started_at, record.session_id


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


ActivityRepository = ActivityRecordRepository
