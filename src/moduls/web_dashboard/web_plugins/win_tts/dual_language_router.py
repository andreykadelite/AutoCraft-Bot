# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any


_ROLE_PRIMARY = "primary"
_ROLE_SECONDARY = "secondary"

_SCRIPT_LATIN = "latin"
_SCRIPT_CYRILLIC = "cyrillic"
_SCRIPT_UNKNOWN = "unknown"

_LANGUAGE_CODE_RE = re.compile(r"([a-z]{2,3})(?:[-_][a-z0-9]{2,8})?", re.IGNORECASE)
_VOICE_LANGUAGE_RE = re.compile(r"([a-z]{2,3})(?:[-_][a-z0-9]{2,8})", re.IGNORECASE)
_SCRIPT_SUBTAG_RE = re.compile(r"(?:^|[-_])(latn|cyrl)(?:$|[-_])", re.IGNORECASE)
_TOKEN_RE = re.compile(r"\s+|[^\W_]+|_+|[^\w\s]+", re.UNICODE)

# Most common BCP-47 language tags that primarily use Cyrillic.
_CYRILLIC_LANGUAGE_CODES = {
    "ab",
    "av",
    "ba",
    "be",
    "bg",
    "bs",
    "ce",
    "cv",
    "kk",
    "ky",
    "mk",
    "mn",
    "ru",
    "sr",
    "tg",
    "tt",
    "uk",
    "uz",
}

# A practical allowlist for languages usually written in Latin.
_LATIN_LANGUAGE_CODES = {
    "af",
    "az",
    "ca",
    "cs",
    "cy",
    "da",
    "de",
    "el",
    "en",
    "es",
    "et",
    "eu",
    "fi",
    "fr",
    "ga",
    "gl",
    "hr",
    "hu",
    "id",
    "is",
    "it",
    "la",
    "lt",
    "lv",
    "ms",
    "mt",
    "nl",
    "no",
    "pl",
    "pt",
    "ro",
    "sk",
    "sl",
    "sq",
    "sv",
    "sw",
    "tl",
    "tr",
    "vi",
}


@dataclass(frozen=True)
class DualLanguageSegment:
    index: int
    role: str
    language: str
    text: str
    letter_count: int


@dataclass(frozen=True)
class DualLanguageRoutingPlan:
    enabled: bool
    active: bool
    primary_language: str
    secondary_language: str
    primary_script: str
    secondary_script: str
    segments: tuple[DualLanguageSegment, ...]
    total_chars: int
    primary_letters: int
    secondary_letters: int
    summary: str

    def as_payload(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "active": bool(self.active),
            "primary_language": self.primary_language,
            "secondary_language": self.secondary_language,
            "primary_script": self.primary_script,
            "secondary_script": self.secondary_script,
            "total_chars": int(self.total_chars),
            "primary_letters": int(self.primary_letters),
            "secondary_letters": int(self.secondary_letters),
            "summary": self.summary,
            "segments": [
                {
                    "index": int(item.index),
                    "role": item.role,
                    "language": item.language,
                    "text": item.text,
                    "letter_count": int(item.letter_count),
                }
                for item in self.segments
            ],
        }


def normalize_language_code(value: Any, fallback: str = "und") -> str:
    text = str(value or "").strip().lower()
    if not text:
        return fallback
    text = "".join(ch for ch in text if ch.isprintable())
    match = _LANGUAGE_CODE_RE.search(text)
    if not match:
        return fallback
    code = str(match.group(1) or "").strip().lower()
    return code or fallback


def guess_language_from_voice_label(voice_label: Any, fallback: str = "und") -> str:
    text = str(voice_label or "").strip()
    if not text:
        return fallback
    match = _VOICE_LANGUAGE_RE.search(text)
    if not match:
        return fallback
    return normalize_language_code(match.group(1), fallback=fallback)


def resolve_voice_language_code(
    voice_label: Any,
    language_catalog: dict[str, list[str]] | None,
    fallback: str = "und",
) -> str:
    voice = str(voice_label or "").strip()
    if voice and isinstance(language_catalog, dict):
        for language_code, voices in language_catalog.items():
            if not isinstance(voices, list):
                continue
            for item in voices:
                if str(item or "").strip() == voice:
                    return normalize_language_code(language_code, fallback=fallback)
    return guess_language_from_voice_label(voice, fallback=fallback)


def resolve_script_family(language_code: Any, fallback: str = _SCRIPT_UNKNOWN) -> str:
    code = str(language_code or "").strip().lower()
    if not code:
        return fallback
    script_hint = _SCRIPT_SUBTAG_RE.search(code)
    if script_hint:
        script_value = str(script_hint.group(1) or "").strip().lower()
        if script_value == "latn":
            return _SCRIPT_LATIN
        if script_value == "cyrl":
            return _SCRIPT_CYRILLIC

    base = normalize_language_code(code, fallback="")
    if not base:
        return fallback
    if base in _CYRILLIC_LANGUAGE_CODES:
        return _SCRIPT_CYRILLIC
    if base in _LATIN_LANGUAGE_CODES:
        return _SCRIPT_LATIN
    return fallback


def _char_script_family(char: str) -> str:
    if not char or len(char) != 1 or not char.isalpha():
        return _SCRIPT_UNKNOWN
    try:
        name = unicodedata.name(char)
    except Exception:
        return _SCRIPT_UNKNOWN
    if "CYRILLIC" in name:
        return _SCRIPT_CYRILLIC
    if "LATIN" in name:
        return _SCRIPT_LATIN
    return _SCRIPT_UNKNOWN


def _count_letters_for_script(value: str, script: str) -> int:
    target = str(script or "").strip().lower()
    if target not in {_SCRIPT_LATIN, _SCRIPT_CYRILLIC}:
        return 0
    return sum(1 for ch in str(value or "") if _char_script_family(ch) == target)


def _token_script_family(token: str) -> str:
    latin_count = 0
    cyrillic_count = 0
    for char in str(token or ""):
        family = _char_script_family(char)
        if family == _SCRIPT_LATIN:
            latin_count += 1
        elif family == _SCRIPT_CYRILLIC:
            cyrillic_count += 1
    if latin_count <= 0 and cyrillic_count <= 0:
        return _SCRIPT_UNKNOWN
    if latin_count > cyrillic_count:
        return _SCRIPT_LATIN
    if cyrillic_count > latin_count:
        return _SCRIPT_CYRILLIC
    return _SCRIPT_UNKNOWN


def _build_fallback_plan(
    text: str,
    enabled: bool,
    primary_language: str,
    secondary_language: str,
    primary_script: str,
    secondary_script: str,
    summary: str,
) -> DualLanguageRoutingPlan:
    clean_text = str(text or "")
    primary_letters = _count_letters_for_script(clean_text, primary_script)
    secondary_letters = _count_letters_for_script(clean_text, secondary_script)
    segment = DualLanguageSegment(
        index=1,
        role=_ROLE_PRIMARY,
        language=primary_language,
        text=clean_text,
        letter_count=primary_letters,
    )
    return DualLanguageRoutingPlan(
        enabled=bool(enabled),
        active=False,
        primary_language=primary_language,
        secondary_language=secondary_language,
        primary_script=primary_script,
        secondary_script=secondary_script,
        segments=(segment,),
        total_chars=len(clean_text),
        primary_letters=primary_letters,
        secondary_letters=secondary_letters,
        summary=str(summary or "").strip() or "Dual-language routing is disabled.",
    )


def build_dual_language_routing_plan(
    text: str,
    enabled: bool,
    primary_language: Any,
    secondary_language: Any,
) -> DualLanguageRoutingPlan:
    clean_text = str(text or "")
    primary_code = normalize_language_code(primary_language, fallback="und")
    secondary_code = normalize_language_code(secondary_language, fallback="und")
    primary_script = resolve_script_family(primary_code, fallback=_SCRIPT_UNKNOWN)
    secondary_script = resolve_script_family(secondary_code, fallback=_SCRIPT_UNKNOWN)

    if not enabled:
        return _build_fallback_plan(
            clean_text,
            False,
            primary_code,
            secondary_code,
            primary_script,
            secondary_script,
            "Dual-language mode is disabled.",
        )
    if not clean_text.strip():
        return _build_fallback_plan(
            clean_text,
            True,
            primary_code,
            secondary_code,
            primary_script,
            secondary_script,
            "Input text is empty.",
        )
    if primary_script == _SCRIPT_UNKNOWN or secondary_script == _SCRIPT_UNKNOWN:
        return _build_fallback_plan(
            clean_text,
            True,
            primary_code,
            secondary_code,
            primary_script,
            secondary_script,
            (
                "Dual-language routing requires resolvable scripts for both languages "
                f"(primary={primary_code}, secondary={secondary_code})."
            ),
        )
    if primary_script == secondary_script:
        return _build_fallback_plan(
            clean_text,
            True,
            primary_code,
            secondary_code,
            primary_script,
            secondary_script,
            "Primary and secondary languages use the same script; switching is skipped.",
        )

    tokens = _TOKEN_RE.findall(clean_text)
    if not tokens:
        tokens = [clean_text]

    segments_raw: list[tuple[str, str]] = []
    current_role = _ROLE_PRIMARY
    current_text = ""
    leading_neutral = ""

    for token in tokens:
        token_script = _token_script_family(token)
        if token_script == secondary_script:
            role = _ROLE_SECONDARY
        elif token_script == primary_script:
            role = _ROLE_PRIMARY
        else:
            role = "neutral"

        if role == "neutral":
            if current_text:
                current_text += token
            else:
                leading_neutral += token
            continue

        normalized_token = f"{leading_neutral}{token}" if leading_neutral else token
        leading_neutral = ""
        if current_text and current_role == role:
            current_text += normalized_token
            continue
        if current_text:
            segments_raw.append((current_role, current_text))
        current_role = role
        current_text = normalized_token

    if current_text:
        segments_raw.append((current_role, current_text))
    elif leading_neutral:
        segments_raw.append((_ROLE_PRIMARY, leading_neutral))

    if not segments_raw:
        return _build_fallback_plan(
            clean_text,
            True,
            primary_code,
            secondary_code,
            primary_script,
            secondary_script,
            "No speakable segments were detected for routing.",
        )

    if leading_neutral and segments_raw:
        first_role, first_text = segments_raw[0]
        segments_raw[0] = (first_role, f"{leading_neutral}{first_text}")

    segments: list[DualLanguageSegment] = []
    primary_letters = 0
    secondary_letters = 0

    for index, (role, chunk_text) in enumerate(segments_raw, start=1):
        language = secondary_code if role == _ROLE_SECONDARY else primary_code
        letter_count = _count_letters_for_script(chunk_text, secondary_script if role == _ROLE_SECONDARY else primary_script)
        if role == _ROLE_SECONDARY:
            secondary_letters += letter_count
        else:
            primary_letters += letter_count
        segments.append(
            DualLanguageSegment(
                index=index,
                role=role,
                language=language,
                text=chunk_text,
                letter_count=letter_count,
            )
        )

    if secondary_letters <= 0:
        return _build_fallback_plan(
            clean_text,
            True,
            primary_code,
            secondary_code,
            primary_script,
            secondary_script,
            "Secondary script was not detected in text; using primary voice only.",
        )

    summary = (
        f"Dual-language routing active: {len(segments)} segment(s), "
        f"primary letters={primary_letters}, secondary letters={secondary_letters}."
    )
    return DualLanguageRoutingPlan(
        enabled=True,
        active=True,
        primary_language=primary_code,
        secondary_language=secondary_code,
        primary_script=primary_script,
        secondary_script=secondary_script,
        segments=tuple(segments),
        total_chars=len(clean_text),
        primary_letters=primary_letters,
        secondary_letters=secondary_letters,
        summary=summary,
    )

