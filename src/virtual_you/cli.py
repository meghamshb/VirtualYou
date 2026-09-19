"""Command-line entry points for the ingestion service."""

import argparse
import json
import sys
from importlib import import_module
from pathlib import Path
from typing import Any, Optional, Sequence

from virtual_you.contracts.activity import ActivityRecord
from virtual_you.ingest.errors import IngestionError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="virtual-you")
    commands = parser.add_subparsers(dest="command", required=True)

    ingest = commands.add_parser("ingest", help="ingest one activity source")
    ingest.add_argument("path", type=Path)
    ingest.add_argument("--source", required=True, choices=("claude", "cursor", "voice"))

    watch = commands.add_parser("watch", help="watch an append-only activity source")
    watch.add_argument("path", type=Path)
    watch.add_argument("--source", required=True, choices=("claude", "cursor"))

    commands.add_parser("latest", help="show the latest sanitized activity")
    commands.add_parser("schema", help="print the ActivityRecord JSON Schema")
    return parser


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    service: Optional[Any] = None,
) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    ingestion_service = service or _create_service(parser)

    try:
        if arguments.command == "ingest":
            result = ingestion_service.ingest(
                path=arguments.path,
                source=arguments.source,
            )
        elif arguments.command == "watch":
            result = ingestion_service.watch(
                path=arguments.path,
                source=arguments.source,
            )
        elif arguments.command == "latest":
            result = ingestion_service.latest_activity()
        elif arguments.command == "schema":
            result = ActivityRecord.model_json_schema()
        else:
            parser.error("unknown command")
            return 2
    except IngestionError as error:
        print(json.dumps(error.as_dict(), sort_keys=True), file=sys.stderr)
        return 1

    if result is not None:
        print(_serialize(result))
    return 0


def _create_service(parser: argparse.ArgumentParser) -> Any:
    try:
        module = import_module("virtual_you.ingest.service")
        service_class = getattr(module, "IngestionService")
        return service_class()
    except (ImportError, AttributeError, TypeError) as exc:
        parser.error(
            "IngestionService is not available; inject a service or complete "
            "virtual_you.ingest.service ({})".format(exc)
        )
        raise AssertionError("argparse.error always exits")


def _serialize(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, indent=2, sort_keys=True, default=str)


if __name__ == "__main__":
    raise SystemExit(main())
