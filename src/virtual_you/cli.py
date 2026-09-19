"""Command-line entry points for the ingestion service."""

import argparse
import json
import os
import sys
import time
from importlib import import_module
from pathlib import Path
from typing import Any, Callable, NoReturn, Optional, Sequence

from virtual_you.contracts.activity import ActivityRecord
from virtual_you.ingest.discover import discover_latest_session
from virtual_you.ingest.errors import IngestionError
from virtual_you.mcp.app import create_app
from virtual_you.mcp.followup import answer
from virtual_you.mcp.github import RestGitHubClient
from virtual_you.mcp.observations import ObservationStore
from virtual_you.mcp.reconcile import reconcile

try:
    import uvicorn
except ImportError:
    uvicorn = None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="virtual-you")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="directory for sanitized records and checkpoints (default: ~/.virtual-you)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    ingest = commands.add_parser("ingest", help="ingest one activity source")
    ingest.add_argument(
        "path",
        nargs="?",
        type=Path,
        help="session file to ingest; omit when using --latest",
    )
    ingest.add_argument(
        "--source",
        required=True,
        choices=("claude", "cursor", "codex", "voice"),
    )
    ingest.add_argument(
        "--latest",
        action="store_true",
        help="ingest the newest local session for --source instead of a path",
    )

    watch = commands.add_parser("watch", help="watch an append-only activity source")
    watch.add_argument(
        "path",
        nargs="?",
        type=Path,
        help="session file to watch; omit when using --latest",
    )
    watch.add_argument("--source", required=True, choices=("claude", "cursor", "codex"))
    watch.add_argument(
        "--latest",
        action="store_true",
        help="watch the newest local session for --source instead of a path",
    )
    watch.add_argument(
        "--follow",
        action="store_true",
        help="keep polling for newly appended JSONL lines",
    )
    watch.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="seconds between --follow polls (default: 1.0)",
    )

    commands.add_parser("latest", help="show the latest sanitized activity")
    commands.add_parser("schema", help="print the ActivityRecord JSON Schema")

    webhook = commands.add_parser(
        "github-webhook",
        help="receive GitHub webhooks (ack, then record observations)",
    )
    webhook.add_argument("--host", default="127.0.0.1")
    webhook.add_argument("--port", type=int, default=8080)

    commands.add_parser(
        "github-reconcile",
        help="fetch missed GitHub events for known SHAs (does not send)",
    )
    ask = commands.add_parser(
        "github-ask",
        help="answer a targeted GitHub follow-up from evidence",
    )
    ask.add_argument("question")
    return parser


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    service: Optional[Any] = None,
    discover: Optional[Callable[..., Path]] = None,
) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    data_directory = (
        arguments.data_dir.expanduser() if arguments.data_dir is not None else None
    )
    ingestion_service = service or _create_service(parser, data_directory)
    discover_fn = discover or discover_latest_session

    try:
        if arguments.command == "ingest":
            result = ingestion_service.ingest(
                path=_resolve_source_path(arguments, parser, discover_fn),
                source=arguments.source,
            )
        elif arguments.command == "watch":
            source_path = _resolve_source_path(arguments, parser, discover_fn)
            if arguments.follow:
                return _watch_follow(
                    ingestion_service,
                    path=source_path,
                    source=arguments.source,
                    interval=arguments.interval,
                )
            result = ingestion_service.watch(
                path=source_path,
                source=arguments.source,
            )
        elif arguments.command == "latest":
            result = ingestion_service.latest_activity()
        elif arguments.command == "schema":
            result = ActivityRecord.model_json_schema()
        elif arguments.command == "github-webhook":
            return _run_github_webhook(
                data_directory,
                host=arguments.host,
                port=arguments.port,
            )
        elif arguments.command == "github-reconcile":
            result = _run_github_reconcile(data_directory)
        elif arguments.command == "github-ask":
            result = _run_github_ask(ingestion_service, arguments.question)
        else:
            _assert_never(arguments.command)
    except IngestionError as error:
        print(json.dumps(error.as_dict(), sort_keys=True), file=sys.stderr)
        return 1

    if result is not None:
        print(_serialize(result))
    return 0


def _watch_follow(
    service: Any,
    *,
    path: Path,
    source: str,
    interval: float,
) -> int:
    poll_interval = interval if interval > 0 else 1.0
    try:
        while True:
            try:
                result = service.watch(path=path, source=source)
            except IngestionError as error:
                print(json.dumps(error.as_dict(), sort_keys=True), file=sys.stderr)
                return 1
            if result is not None:
                print(_serialize(result))
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        return 0


def _resolve_source_path(
    arguments: argparse.Namespace,
    parser: argparse.ArgumentParser,
    discover: Callable[..., Path],
) -> Path:
    if arguments.path is not None:
        return arguments.path
    if arguments.latest:
        return discover(arguments.source)
    parser.error("provide a path or --latest")
    raise AssertionError("argparse.error always exits")


def _data_root(data_directory: Optional[Path]) -> Path:
    if data_directory is not None:
        return data_directory
    configured = os.environ.get("VIRTUAL_YOU_DATA_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".virtual-you"


def _run_github_webhook(
    data_directory: Optional[Path],
    *,
    host: str,
    port: int,
) -> int:
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "").strip()
    if not secret:
        print(
            json.dumps({"error": "GITHUB_WEBHOOK_SECRET is required"}),
            file=sys.stderr,
        )
        return 1
    if uvicorn is None:
        print(
            json.dumps({"error": "Install virtual-you-ingest[github] to serve webhooks"}),
            file=sys.stderr,
        )
        return 1
    store = ObservationStore(_data_root(data_directory))
    app = create_app(store, secret)
    uvicorn.run(app, host=host, port=port)
    return 0


def _run_github_reconcile(data_directory: Optional[Path]) -> dict:
    store = ObservationStore(_data_root(data_directory))
    client = RestGitHubClient.from_env(os.environ)
    if client is None:
        return {"written": 0, "error": "github_unconfigured"}
    written = reconcile(store, client)
    return {"written": written}


def _run_github_ask(service: Any, question: str) -> dict:
    record = service.latest_activity()
    if record is None:
        return {"escalated": True, "text": "No activity record is stored."}
    store = ObservationStore(service._root)
    client = getattr(service, "_github_client", None) or RestGitHubClient.from_env(
        os.environ
    )
    result = answer(
        question,
        record=record,
        observations=store.all(),
        client=client,
    )
    return {
        "escalated": result.escalated,
        "kind": result.kind,
        "text": result.text,
    }


def _create_service(
    parser: argparse.ArgumentParser,
    data_directory: Optional[Path] = None,
) -> Any:
    try:
        module = import_module("virtual_you.ingest.service")
        service_class = getattr(module, "IngestionService")
        if data_directory is None:
            return service_class()
        return service_class(data_directory=data_directory)
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


def _assert_never(value: str) -> NoReturn:
    raise AssertionError("unhandled command: {}".format(value))


if __name__ == "__main__":
    raise SystemExit(main())
