"""One JSON generation interface; provider configuration never changes workflow rules."""

from __future__ import annotations

import json
import re
from typing import Protocol

import httpx

from virtual_you.backend.errors import ServiceError
from virtual_you.backend.style import extract_style
from virtual_you.contracts.reporting import SECTION_TITLES

UNKNOWN = "Not recorded in the selected activity."


class JsonProvider(Protocol):
    name: str

    async def generate(self, *, task: str, system: str, user: str, schema: dict) -> dict: ...


class HttpProvider:
    def __init__(self, settings, client: httpx.AsyncClient):
        self.settings, self.client = settings, client
        self.name = settings.provider + ":" + settings.model

    async def generate(self, *, task, system, user, schema):
        schema_text = json.dumps(schema)
        if schema_text not in system:
            system += "\nRequired JSON response schema: " + schema_text
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        try:
            if self.settings.provider == "openai":
                response = await self.client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": "Bearer " + self.settings.openai_api_key},
                    json={
                        "model": self.settings.model,
                        "messages": messages,
                        "response_format": {"type": "json_object"},
                    },
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
            else:
                response = await self.client.post(
                    self.settings.ollama_url.rstrip("/") + "/api/chat",
                    json={
                        "model": self.settings.model,
                        "messages": messages,
                        "stream": False,
                        "format": schema,
                    },
                )
                response.raise_for_status()
                content = response.json()["message"]["content"]
            if len(content) > 100_000:
                raise ValueError("Oversized model response")
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ValueError("Expected JSON object")
            return result
        except (httpx.HTTPError, KeyError, TypeError, ValueError, IndexError) as error:
            raise ServiceError(
                "model_unavailable",
                "Model request failed or returned invalid JSON. Check server-side provider configuration.",
                502,
            ) from error


class DemoProvider:
    """Explicit offline fixture mode, not an LLM and never a silent fallback."""

    name = "demo:extractive"

    async def generate(self, *, task, system, user, schema):
        data = json.loads(user)
        if task == "persona":
            return extract_style(data["messages"])
        if task == "grounded_reply":
            evidence = data["evidence"]
            selected = next((e for e in evidence if e["field"] == "end_state"), None)
            return {"paragraphs": [{"text": selected["text"][:450] if selected else UNKNOWN,
                                     "citations": [{"evidence_id": selected["evidence_id"], "quote": selected["text"][:450]}] if selected else []}],
                    "search_query": ""}
        evidence = data["evidence"]
        report = {key: {"text": UNKNOWN, "citations": []} for key in SECTION_TITLES}

        def section(key, entries):
            entries = entries[:3]
            if entries:
                report[key] = {
                    "text": "\n".join(e["text"][:450] for e in entries),
                    "citations": [
                        {"evidence_id": e["evidence_id"], "quote": e["text"][:450]} for e in entries
                    ],
                }

        section("starting_state", [e for e in evidence if e["field"] == "start_state"])
        approaches = []
        for entry in evidence:
            if entry["field"] != "reasoning_summary" or "chain-of-thought" in entry["text"]:
                continue
            text = entry["text"]
            # Phase 1.1 may append visible explanations after this omission marker.
            marker = "Reasoning occurred; omitted."
            if text.startswith(marker):
                text = text[len(marker) :].strip()
                if text.startswith("Approach:"):
                    text = text[len("Approach:") :].strip()
            if text:
                approaches.append({**entry, "text": text})
        section("approach", approaches)
        section("changes", [e for e in evidence if e["field"].startswith("files_changed")])
        section("result", [e for e in evidence if e["field"] == "end_state"])
        section("links", [e for e in evidence if re.search(r"https?://", e["text"])])
        section("blockers", [e for e in evidence if "[failed]" in e["text"]])
        return report


def make_provider(settings, client):
    return DemoProvider() if settings.provider == "demo" else HttpProvider(settings, client)
