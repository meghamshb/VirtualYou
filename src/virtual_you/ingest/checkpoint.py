"""Restart-safe incremental reading for append-only JSONL files."""

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Union


PathLike = Union[str, os.PathLike[str]]


@dataclass(frozen=True)
class Checkpoint:
    """The byte position and uncompleted line saved between reads."""

    offset: int = 0
    buffer: bytes = b""


class JsonCheckpointStore:
    """Persist a reader checkpoint as an atomically replaced JSON file."""

    def __init__(self, path: PathLike) -> None:
        self.path = Path(path)

    def load(self) -> Checkpoint:
        if not self.path.exists():
            return Checkpoint()

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        offset = payload.get("offset")
        encoded_buffer = payload.get("buffer", "")
        if not isinstance(offset, int) or offset < 0:
            raise ValueError("checkpoint offset must be a non-negative integer")
        if not isinstance(encoded_buffer, str):
            raise ValueError("checkpoint buffer must be a base64 string")

        try:
            buffer = base64.b64decode(encoded_buffer.encode("ascii"), validate=True)
        except (ValueError, UnicodeEncodeError) as exc:
            raise ValueError("checkpoint buffer is not valid base64") from exc
        return Checkpoint(offset=offset, buffer=buffer)

    def save(self, checkpoint: Checkpoint) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        payload = {
            "buffer": base64.b64encode(checkpoint.buffer).decode("ascii"),
            "offset": checkpoint.offset,
            "version": 1,
        }
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(self.path)

    def reset(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


class IncrementalJsonlReader:
    """Read only newly completed JSONL records.

    The checkpoint advances past every byte read and carries an incomplete final
    line. Consequently, polling and process restarts neither duplicate completed
    lines nor discard a line that is still being appended.
    """

    def __init__(self, source_path: PathLike, checkpoint_path: PathLike) -> None:
        self.source_path = Path(source_path)
        self.checkpoints = JsonCheckpointStore(checkpoint_path)

    def read_available(self) -> List[Dict[str, Any]]:
        checkpoint = self.checkpoints.load()
        file_size = self.source_path.stat().st_size
        if file_size < checkpoint.offset:
            checkpoint = Checkpoint()

        with self.source_path.open("rb") as source:
            source.seek(checkpoint.offset)
            appended = source.read()

        if not appended:
            return []

        data = checkpoint.buffer + appended
        complete_lines, pending = self._partition_lines(data)
        records = [self._decode_line(line) for line in complete_lines if line.strip()]

        self.checkpoints.save(
            Checkpoint(
                offset=checkpoint.offset + len(appended),
                buffer=pending,
            )
        )
        return records

    def read(self) -> List[Dict[str, Any]]:
        """Alias for callers that model each poll as a read."""

        return self.read_available()

    @staticmethod
    def _partition_lines(data: bytes) -> tuple:
        lines = data.splitlines(keepends=True)
        if lines and not lines[-1].endswith((b"\n", b"\r")):
            return lines[:-1], lines[-1]
        return lines, b""

    @staticmethod
    def _decode_line(line: bytes) -> Dict[str, Any]:
        payload = json.loads(line.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("each JSONL line must contain a JSON object")
        return payload


IncrementalJSONLReader = IncrementalJsonlReader
