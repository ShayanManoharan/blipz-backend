"""Versioned deterministic answer normalization; no fuzzy or semantic grading."""
import unicodedata
from collections.abc import Iterable

NORMALIZATION_VERSION = "answer-normalization-v1"


def normalize_answer(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Answer must be text")
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split()).rstrip(".!?")


def matches_alias(value: str, approved_aliases: Iterable[str]) -> bool:
    normalized = normalize_answer(value)
    return bool(normalized) and normalized in {normalize_answer(alias) for alias in approved_aliases}
