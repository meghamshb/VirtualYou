"""Recursive, fail-closed secret redaction for ingestion output."""

import json
import math
import re
from collections import Counter
from dataclasses import fields, is_dataclass, replace
from datetime import date, datetime
from enum import Enum
from typing import Any, Optional, Sequence, Set

from pydantic import BaseModel

from virtual_you.ingest.errors import IngestionError, IngestionErrorCode


REDACTED = "[REDACTED]"

_SENSITIVE_LEAF_PATTERN = (
    r"(?:api[_-]?key|access[_-]?key|access[_-]?token|auth[_-]?token|token|"
    r"password|passwd|pwd|secret|client[_-]?secret|private[_-]?key|signature|"
    r"database[_-]?url|db[_-]?url|"
    r"aws[_-]?access[_-]?key[_-]?id|aws[_-]?secret[_-]?access[_-]?key|"
    r"aws[_-]?session[_-]?token)"
)
_SENSITIVE_KEY_PATTERN = r"(?:[A-Za-z][A-Za-z0-9]*_)*" + _SENSITIVE_LEAF_PATTERN
_SENSITIVE_KEY_RE = re.compile(r"(?i)^{}$".format(_SENSITIVE_KEY_PATTERN))
_PEM_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----.*?"
    r"-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----",
    re.DOTALL,
)
_AUTH_RE = re.compile(
    r"(?i)(\b(?:proxy-)?authorization\s*:\s*(?:bearer|basic)\s+)"
    r"[A-Za-z0-9._~+/=-]+"
)
_INLINE_AUTH_RE = re.compile(
    r"(?i)(\b(?:bearer|basic)\s+)[A-Za-z0-9._~+/=-]{8,}"
)
_SENSITIVE_QUERY_RE = re.compile(
    r"(?i)([?&]{}=)([^&#\s]+)".format(_SENSITIVE_KEY_PATTERN)
)
_QUOTED_KEY_VALUE_RE = re.compile(
    r"""(?ix)(\b{}\b["']?\s*(?:=|:)\s*)(["'])(.*?)(\2)""".format(
        _SENSITIVE_KEY_PATTERN
    )
)
_UNQUOTED_KEY_VALUE_RE = re.compile(
    r"""(?ix)(\b{}\b["']?\s*(?:=|:)\s*)(?!\[REDACTED\])([^\s,;}}\]]+)""".format(
        _SENSITIVE_KEY_PATTERN
    )
)
_KNOWN_SECRET_RES = (
    re.compile(r"(?<![A-Za-z0-9])sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?<![A-Za-z0-9])sk-ant-[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"(?<![A-Za-z0-9])xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])"),
)
_ENTROPY_CANDIDATE_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z0-9_+=.-]{24,}(?![A-Za-z0-9_])")
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)


def _redact_quoted_assignment(match: re.Match) -> str:
    return "{}{}{}{}".format(match.group(1), match.group(2), REDACTED, match.group(4))


def _redact_prefixed(match: re.Match) -> str:
    return "{}{}".format(match.group(1), REDACTED)


def _shannon_entropy(value: str) -> float:
    counts = Counter(value)
    length = float(len(value))
    return -sum((count / length) * math.log(count / length, 2) for count in counts.values())


def _looks_like_high_entropy_secret(text: str, match: re.Match) -> bool:
    value = match.group(0)
    if value == REDACTED or _UUID_RE.fullmatch(value):
        return False
    if match.start() > 0 and text[match.start() - 1] in "/\\":
        return False
    if match.end() < len(text) and text[match.end()] in "/\\":
        return False
    character_classes = sum(
        (
            any(character.islower() for character in value),
            any(character.isupper() for character in value),
            any(character.isdigit() for character in value),
            any(character in "_+=.-" for character in value),
        )
    )
    return character_classes >= 3 and len(set(value)) >= 12 and _shannon_entropy(value) >= 4.0


def _redact_entropy_candidate(text: str, match: re.Match) -> str:
    if _looks_like_high_entropy_secret(text, match):
        return REDACTED
    return match.group(0)


def is_sensitive_key(name: str) -> bool:
    """Return whether a mapping or dotenv key should have its value redacted."""

    return bool(name) and _SENSITIVE_KEY_RE.fullmatch(name.strip()) is not None


def _apply_extra_secrets(text: str, extra_secrets: Sequence[str]) -> str:
    redacted = text
    for secret in extra_secrets:
        if not secret or secret == REDACTED:
            continue
        redacted = redacted.replace(secret, REDACTED)
    return redacted


def redact_text(text: str, extra_secrets: Sequence[str] = ()) -> str:
    """Replace secrets in text without returning their values."""
    if not isinstance(text, str):
        raise TypeError("redact_text requires a string")

    redacted = text
    for _ in range(8):
        previous = redacted
        redacted = _apply_extra_secrets(redacted, extra_secrets)
        redacted = _PEM_PRIVATE_KEY_RE.sub(REDACTED, redacted)
        redacted = _AUTH_RE.sub(_redact_prefixed, redacted)
        redacted = _INLINE_AUTH_RE.sub(_redact_prefixed, redacted)
        redacted = _SENSITIVE_QUERY_RE.sub(_redact_prefixed, redacted)
        redacted = _QUOTED_KEY_VALUE_RE.sub(_redact_quoted_assignment, redacted)
        redacted = _UNQUOTED_KEY_VALUE_RE.sub(_redact_prefixed, redacted)
        for secret_re in _KNOWN_SECRET_RES:
            redacted = secret_re.sub(REDACTED, redacted)
        redacted = _ENTROPY_CANDIDATE_RE.sub(
            lambda match: _redact_entropy_candidate(redacted, match),
            redacted,
        )
        if redacted == previous:
            return redacted
    return redacted


def redact_value(
    value: Any,
    _seen: Optional[Set[int]] = None,
    *,
    extra_secrets: Sequence[str] = (),
) -> Any:
    """Recursively redact strings while preserving common container/model types."""
    seen = set() if _seen is None else _seen
    if isinstance(value, str):
        return redact_text(value, extra_secrets=extra_secrets)
    if isinstance(value, bytes):
        return redact_text(
            value.decode("utf-8", errors="replace"),
            extra_secrets=extra_secrets,
        ).encode("utf-8")
    if value is None or isinstance(
        value,
        (bool, int, float, Enum, date, datetime),
    ):
        return value

    identity = id(value)
    if identity in seen:
        raise IngestionError(
            IngestionErrorCode.UNSAFE_OUTPUT,
            "Output could not be safely redacted.",
        )
    seen.add(identity)
    try:
        if isinstance(value, BaseModel):
            updates = {
                name: (
                    REDACTED
                    if is_sensitive_key(name)
                    else redact_value(
                        getattr(value, name),
                        seen,
                        extra_secrets=extra_secrets,
                    )
                )
                for name in type(value).model_fields
            }
            return value.model_copy(update=updates)
        if is_dataclass(value) and not isinstance(value, type):
            updates = {
                field.name: (
                    REDACTED
                    if is_sensitive_key(field.name)
                    else redact_value(
                        getattr(value, field.name),
                        seen,
                        extra_secrets=extra_secrets,
                    )
                )
                for field in fields(value)
            }
            return replace(value, **updates)
        if isinstance(value, dict):
            return {
                redact_value(key, seen, extra_secrets=extra_secrets): (
                    REDACTED
                    if isinstance(key, str) and is_sensitive_key(key)
                    else redact_value(item, seen, extra_secrets=extra_secrets)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [
                redact_value(item, seen, extra_secrets=extra_secrets) for item in value
            ]
        if isinstance(value, tuple):
            return tuple(
                redact_value(item, seen, extra_secrets=extra_secrets) for item in value
            )
        if isinstance(value, set):
            return {
                redact_value(item, seen, extra_secrets=extra_secrets) for item in value
            }
        raise IngestionError(
            IngestionErrorCode.UNSAFE_OUTPUT,
            "Output contains a value that cannot be safely redacted.",
        )
    finally:
        seen.remove(identity)


def _serialized_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, BaseModel):
        return value.model_dump_json()
    try:
        return json.dumps(value, default=str, sort_keys=True)
    except (TypeError, ValueError, OverflowError):
        raise IngestionError(
            IngestionErrorCode.UNSAFE_OUTPUT,
            "Output could not be inspected safely.",
        )


def contains_secret(value: Any, extra_secrets: Sequence[str] = ()) -> bool:
    """Return whether a serialized value contains a recognized secret."""
    serialized = _serialized_text(value)
    return redact_text(serialized, extra_secrets=extra_secrets) != serialized


def assert_safe_serialized(
    value: Any, extra_secrets: Sequence[str] = ()
) -> None:
    """Raise a stable, secret-safe error if serialized output contains a secret."""
    if contains_secret(value, extra_secrets=extra_secrets):
        raise IngestionError(
            IngestionErrorCode.UNSAFE_OUTPUT,
            "Serialized output contains sensitive data.",
        )
