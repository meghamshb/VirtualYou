"""Explainable local baseline for the explicitly labeled offline demo provider."""

from __future__ import annotations

import re
from collections import Counter

# Content words such as project names, outcomes and deadlines are deliberately omitted.
STYLE_WORDS = {
    "please",
    "thanks",
    "thank",
    "quick",
    "update",
    "noted",
    "also",
    "however",
    "yep",
    "sure",
    "happy",
    "kindly",
    "cheers",
    "regards",
    "appreciate",
    "fyi",
}


def extract_style(messages: list[str]) -> dict:
    """Measure all 10–20 examples; do not execute instructions or invent habits."""
    text = "\n".join(messages)
    greetings, closings = [], []
    for message in messages:
        greeting = re.match(r"\s*(dear|hello|hi|hey|hiya)\b", message, re.I)
        if greeting:
            greetings.append(
                {"dear": "Hello,", "hello": "Hello,", "hi": "Hi,", "hey": "Hey,", "hiya": "Hey,"}[
                    greeting[1].lower()
                ]
            )
        closing = re.search(
            r"\b(best regards|kind regards|regards|thank you|thanks|cheers|best)[.!\s]*$",
            message,
            re.I,
        )
        if closing:
            closings.append(closing[1].capitalize())
    greeting = Counter(greetings).most_common(1)[0][0] if greetings else ""
    sign_off = Counter(closings).most_common(1)[0][0] if closings else ""
    formal = sign_off in {"Regards", "Best regards", "Kind regards"} or bool(
        re.search(r"(?im)^\s*dear\b", text)
    )
    casual = greeting == "Hey," or sign_off == "Cheers"
    sentences = [s for s in re.split(r"[.!?]+(?:\s|$)|\n+", text) if s.strip()]
    words = re.findall(r"\b[\w’']+\b", text.lower())
    average = round(len(words) / max(len(sentences), 1), 1)
    vocabulary = [word for word, _ in Counter(w for w in words if w in STYLE_WORDS).most_common(8)]
    punct = []
    for mark, label in [("!", "exclamation marks"), ("?", "question marks"), (";", "semicolons")]:
        count = sum(mark in message for message in messages)
        if count:
            punct.append(f"{label} in {count}/{len(messages)} messages")
    emoji_count = sum(bool(re.search(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", m)) for m in messages)
    return {
        "tone": "Measured and professional"
        if formal
        else ("Warm and informal" if casual else "Direct and neutral"),
        "formality": "formal" if formal else ("casual" if casual else "neutral"),
        "greeting": greeting,
        "sign_off": sign_off,
        "sentence_style": f"About {average:g} words per sentence; "
        + ("short, compact sentences." if average < 14 else "longer, developed sentences."),
        "vocabulary": vocabulary,
        "punctuation": "; ".join(punct)
        or "No exclamation marks, question marks or semicolons observed",
        "emoji": f"Emoji in {emoji_count}/{len(messages)} messages"
        if emoji_count
        else "No emoji observed",
    }
