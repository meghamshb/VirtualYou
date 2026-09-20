"""Trusted conversational writing guidance and bounded pre-review quality checks."""

import re

from virtual_you.backend.errors import ServiceError

REPLY_WRITING_GUIDANCE = (
    "Write for a teammate who wants the meaning of the work, not a Git log. "
    "Answer first with the most relevant recorded outcome in plain English. "
    "For several changes, use 2–4 short bullets grouped by user-visible behavior; "
    "each bullet should say what someone can now do or what changed, only if supported. "
    "For one focused question, a direct sentence or two is enough. "
    "Use active natural language: 'Added', 'Fixed', 'Restored', 'Connected'. "
    "Do not paste commit subjects verbatim when they read like a changelog; explain their "
    "supported meaning without adding benefits or validation that the evidence does not establish. "
    "When a record contains only a commit title, report only what the title explicitly says. "
    "Use 'Recorded changes include' when implementation or runtime behavior is not verified. "
    "Do not turn a title into an inferred purpose or benefit using 'to improve', 'to ensure', "
    "'to enable', 'allowing better', 'cleaner development', or similar causal language. "
    "Those relationships need an explicit supporting explanation, not just a change title. "
    "Do not narrate every commit, repeat the same change, dump hashes, enumerate implementation "
    "filenames, or lead with dates. Keep identifiers in citations and the evidence panel. "
    "Only mention a filename or commit reference when it directly answers a technical question; "
    "prefer a short commit ID, and give a full SHA only when explicitly requested. "
    "A broad status reply should usually be 50–100 words and under 800 characters; "
    "do not fill the budget when a shorter answer works. Use at most one brief bold heading "
    "if it helps. Avoid repeated Changes/Result headings, generic praise and dense prose. "
    "Detailed questions may need more detail, but remain under 2200 characters. "
    "Explain why only when the reason is explicitly recorded, never from a filename or guess. "
    "For revert/restore history, explain the latest supported change once instead of presenting "
    "both removed and restored as simultaneously current. For a broad update, prioritize the "
    "newest concrete product changes; omit bare merge/revert bookkeeping unless the user asks "
    "about Git history or the evidence explains its relevant product effect. Never invent a "
    "purpose for a revert. Use dates and source content to "
    "understand order, but a selected newer record does not prove branch ancestry, deployment, "
    "or that no later changes exist. If records conflict without a clear relationship, say the "
    "current state is not established. Historical work is not a current blocker. "
    "Distinguish code added from behavior tested. Git diffs and commit messages alone cannot "
    "prove live operation, successful tests or deployment. Add a brief validation caveat only "
    "when material to the question; do not repeat it after every bullet. "
    "Keep uncertainty and qualifications. No recommendations, promises, predicted benefits, "
    "or invented success. Persona tone never overrides accuracy or the concise structure. "
)


def detailed_reply_requested(question):
    return bool(
        re.search(
            r"\b(detailed|in[- ]depth|exhaustive|verbatim|full (?:diff|explanation|details)|"
            r"step[- ]by[- ]step|list (?:every|all) (?:file|commit|change))\b",
            question,
            re.I,
        )
    )


def validate_reply_style(text, question):
    """Request one rewrite rather than deleting facts or abbreviating after approval."""
    limit = 2400 if detailed_reply_requested(question) else 1200
    if len(text) > limit:
        raise ServiceError(
            "reply_too_long",
            f"Rewrite the reply under {limit} characters, keeping the answer and all material uncertainty. "
            "Group related changes into short outcome-led bullets; omit duplicate history and implementation trivia.",
            502,
        )
    full_hash = re.search(r"\b[0-9a-f]{40}\b", text, re.I)
    wants_full_hash = bool(
        re.search(
            r"\b(?:sha(?:-?1)?|hash(?:es)?|full (?:commit|revision|id)|exact (?:commit|revision|id))\b",
            question,
            re.I,
        )
    ) or bool(re.search(r"\b[0-9a-f]{40}\b", question, re.I))
    if full_hash and not wants_full_hash:
        raise ServiceError(
            "reply_identifier_dump",
            "Explain the supported change in plain language. Remove full commit hashes from the prose; "
            "keep source references in citations. Use a short ID only if needed to answer this question.",
            502,
        )
