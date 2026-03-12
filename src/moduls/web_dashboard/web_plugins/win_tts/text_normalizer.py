# -*- coding: utf-8 -*-
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
import json
import re
import unicodedata
from typing import Any


_ALLOWED_PRESETS = ("soft", "balanced", "aggressive")
_DEFAULT_PRESET = "balanced"
_AUTO_BALANCED_THRESHOLD = 12_000
_AUTO_AGGRESSIVE_THRESHOLD = 60_000

_URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>()]+")
_EMAIL_RE = re.compile(r"(?i)\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,24}\b")
_MARKDOWN_LINK_RE = re.compile(
    r"\[(?P<label>[^\]\n]{1,300})\]\((?P<url>(?:https?://|mailto:|www\.)[^\s)]+)[^)]*\)"
)
_MARKDOWN_IMAGE_RE = re.compile(r"!\[(?P<label>[^\]\n]{0,300})\]\((?P<url>[^\s)]+)[^)]*\)")
_MARKDOWN_FENCE_RE = re.compile(r"`{3,}")
_MARKDOWN_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_MARKDOWN_HEADING_RE = re.compile(r"(?m)^[ \t]{0,3}(?:#{1,6}[ \t]+|>[ \t]+|[-*+][ \t]+)")
_MARKDOWN_STYLE_RE = re.compile(r"(\*{1,3}|_{1,3}|~{1,2})")
_ANALYZE_SYMBOLS = "*#@_^~=+/\\|<>[]{}"
_ANALYZE_SYMBOL_SET = set(_ANALYZE_SYMBOLS)
_ANALYZE_SYMBOL_CLASS_RE = re.escape(_ANALYZE_SYMBOLS)
_REPEAT_COLLAPSIBLE_SYMBOLS = "".join(symbol for symbol in _ANALYZE_SYMBOLS if symbol not in {"/", "\\"})
_REPEAT_COLLAPSIBLE_CLASS_RE = re.escape(_REPEAT_COLLAPSIBLE_SYMBOLS)
_REPEAT_SYMBOL_RE = re.compile(r"([" + _REPEAT_COLLAPSIBLE_CLASS_RE + r"])\1{1,}")
_REPEAT_PUNCT_RE = re.compile(r"([!?.,;:])(?:\s*\1){1,}")
_SYMBOL_TOKEN_RE = re.compile(
    r"(?<![\w" + _ANALYZE_SYMBOL_CLASS_RE + r"])"
    r"[" + _ANALYZE_SYMBOL_CLASS_RE + r"]{1,}"
    r"(?![\w" + _ANALYZE_SYMBOL_CLASS_RE + r"])"
)
_NBSP_RE = re.compile(r"[\u00A0\u202F\u2007]")
_WHITESPACE_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")
_ELLIPSIS_TOKEN_RE = re.compile(r"(?:…|\.{3,})")


def _is_noise_symbol(symbol: str) -> bool:
    char = str(symbol or "")
    if not char or len(char) != 1:
        return False
    if char.isspace():
        return False
    if char in _ANALYZE_SYMBOL_SET:
        return True
    category = unicodedata.category(char)
    return bool(category) and category[0] in {"P", "S"}


@dataclass(frozen=True)
class EdgeTextNormalizerSettings:
    enabled: bool = True
    preset: str = _DEFAULT_PRESET
    auto_tune: bool = True
    strip_markdown: bool = True
    unwrap_markdown_links: bool = True
    strip_urls: bool = True
    strip_emails: bool = True
    collapse_repeated_symbols: bool = True
    collapse_repeated_punctuation: bool = True
    preserve_ellipsis: bool = True
    drop_symbol_only_tokens: bool = True
    normalize_whitespace: bool = True
    drop_symbols: tuple[str, ...] = ()


@dataclass(frozen=True)
class EdgeTextNormalizerResult:
    text: str
    settings: EdgeTextNormalizerSettings
    source_preset: str
    applied_preset: str
    auto_tuned: bool
    changed: bool
    stats: dict[str, int]
    summary: str

    def as_payload(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.settings.enabled),
            "source_preset": self.source_preset,
            "preset": self.applied_preset,
            "auto_tuned": bool(self.auto_tuned),
            "changed": bool(self.changed),
            "summary": self.summary,
            "settings": edge_text_normalizer_settings_payload(self.settings),
            "stats": {key: int(value) for key, value in self.stats.items()},
        }


_PRESET_OVERRIDES: dict[str, dict[str, Any]] = {
    "soft": {
        "strip_markdown": False,
        "unwrap_markdown_links": False,
        "strip_urls": False,
        "strip_emails": False,
        "collapse_repeated_symbols": True,
        "collapse_repeated_punctuation": False,
        "preserve_ellipsis": True,
        "drop_symbol_only_tokens": True,
        "normalize_whitespace": True,
    },
    "balanced": {
        "strip_markdown": True,
        "unwrap_markdown_links": True,
        "strip_urls": True,
        "strip_emails": True,
        "collapse_repeated_symbols": True,
        "collapse_repeated_punctuation": True,
        "preserve_ellipsis": True,
        "drop_symbol_only_tokens": True,
        "normalize_whitespace": True,
    },
    "aggressive": {
        "strip_markdown": True,
        "unwrap_markdown_links": True,
        "strip_urls": True,
        "strip_emails": True,
        "collapse_repeated_symbols": True,
        "collapse_repeated_punctuation": True,
        "preserve_ellipsis": True,
        "drop_symbol_only_tokens": True,
        "normalize_whitespace": True,
    },
}


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "y", "on", "да"}:
        return True
    if text in {"0", "false", "no", "n", "off", "нет"}:
        return False
    return default


def _normalize_preset_name(value: Any, fallback: str = _DEFAULT_PRESET) -> str:
    candidate = str(value or "").strip().lower()
    if candidate in _ALLOWED_PRESETS:
        return candidate
    return fallback


def _parse_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, EdgeTextNormalizerSettings):
        return edge_text_normalizer_settings_payload(raw)
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except Exception:
            return {}
        if isinstance(parsed, dict):
            return dict(parsed)
    return {}


def _normalize_drop_symbols(raw: Any) -> tuple[str, ...]:
    items: list[Any] = []
    if isinstance(raw, (list, tuple, set)):
        items = list(raw)
    elif isinstance(raw, str):
        text = raw.strip()
        if text:
            if text.startswith("["):
                try:
                    parsed = json.loads(text)
                except Exception:
                    parsed = None
                if isinstance(parsed, list):
                    items = list(parsed)
                else:
                    items = list(text)
            elif "," in text:
                items = [chunk.strip() for chunk in text.split(",") if chunk.strip()]
            else:
                items = list(text)
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_item in items:
        for symbol in str(raw_item or ""):
            if not _is_noise_symbol(symbol):
                continue
            if symbol in seen:
                continue
            seen.add(symbol)
            normalized.append(symbol)
    return tuple(normalized[:32])


def edge_text_normalizer_settings_payload(settings: EdgeTextNormalizerSettings) -> dict[str, Any]:
    return {
        "enabled": bool(settings.enabled),
        "preset": _normalize_preset_name(settings.preset),
        "auto_tune": bool(settings.auto_tune),
        "strip_markdown": bool(settings.strip_markdown),
        "unwrap_markdown_links": bool(settings.unwrap_markdown_links),
        "strip_urls": bool(settings.strip_urls),
        "strip_emails": bool(settings.strip_emails),
        "collapse_repeated_symbols": bool(settings.collapse_repeated_symbols),
        "collapse_repeated_punctuation": bool(settings.collapse_repeated_punctuation),
        "preserve_ellipsis": bool(settings.preserve_ellipsis),
        "drop_symbol_only_tokens": bool(settings.drop_symbol_only_tokens),
        "normalize_whitespace": bool(settings.normalize_whitespace),
        "drop_symbols": [item for item in settings.drop_symbols if _is_noise_symbol(item)],
    }


def parse_edge_text_normalizer_settings(raw: Any) -> EdgeTextNormalizerSettings:
    payload = _parse_payload(raw)
    preset = _normalize_preset_name(payload.get("preset"), _DEFAULT_PRESET)
    defaults = dict(_PRESET_OVERRIDES.get(preset) or _PRESET_OVERRIDES[_DEFAULT_PRESET])
    settings = EdgeTextNormalizerSettings(
        enabled=_as_bool(payload.get("enabled"), True),
        preset=preset,
        auto_tune=_as_bool(payload.get("auto_tune"), True),
        strip_markdown=_as_bool(payload.get("strip_markdown"), bool(defaults["strip_markdown"])),
        unwrap_markdown_links=_as_bool(
            payload.get("unwrap_markdown_links"),
            bool(defaults["unwrap_markdown_links"]),
        ),
        strip_urls=_as_bool(payload.get("strip_urls"), bool(defaults["strip_urls"])),
        strip_emails=_as_bool(payload.get("strip_emails"), bool(defaults["strip_emails"])),
        collapse_repeated_symbols=_as_bool(
            payload.get("collapse_repeated_symbols"),
            bool(defaults["collapse_repeated_symbols"]),
        ),
        collapse_repeated_punctuation=_as_bool(
            payload.get("collapse_repeated_punctuation"),
            bool(defaults["collapse_repeated_punctuation"]),
        ),
        preserve_ellipsis=_as_bool(payload.get("preserve_ellipsis"), bool(defaults["preserve_ellipsis"])),
        drop_symbol_only_tokens=_as_bool(
            payload.get("drop_symbol_only_tokens"),
            bool(defaults["drop_symbol_only_tokens"]),
        ),
        normalize_whitespace=_as_bool(payload.get("normalize_whitespace"), bool(defaults["normalize_whitespace"])),
        drop_symbols=_normalize_drop_symbols(payload.get("drop_symbols")),
    )
    return settings


def edge_text_normalizer_config_payload() -> dict[str, Any]:
    default_settings = EdgeTextNormalizerSettings()
    profiles: dict[str, dict[str, Any]] = {}
    for preset_name in _ALLOWED_PRESETS:
        profile_defaults = _PRESET_OVERRIDES.get(preset_name) or _PRESET_OVERRIDES[_DEFAULT_PRESET]
        profile_settings = EdgeTextNormalizerSettings(
            enabled=True,
            preset=preset_name,
            auto_tune=default_settings.auto_tune,
            strip_markdown=bool(profile_defaults["strip_markdown"]),
            unwrap_markdown_links=bool(profile_defaults["unwrap_markdown_links"]),
            strip_urls=bool(profile_defaults["strip_urls"]),
            strip_emails=bool(profile_defaults["strip_emails"]),
            collapse_repeated_symbols=bool(profile_defaults["collapse_repeated_symbols"]),
            collapse_repeated_punctuation=bool(profile_defaults["collapse_repeated_punctuation"]),
            preserve_ellipsis=bool(profile_defaults["preserve_ellipsis"]),
            drop_symbol_only_tokens=bool(profile_defaults["drop_symbol_only_tokens"]),
            normalize_whitespace=bool(profile_defaults["normalize_whitespace"]),
        )
        profiles[preset_name] = edge_text_normalizer_settings_payload(profile_settings)
    return {
        "available": True,
        "default": edge_text_normalizer_settings_payload(default_settings),
        "profiles": profiles,
        "presets": [
            {
                "id": "soft",
                "label": "Мягкий",
                "description": "Минимально вмешивается: сжимает повторяющиеся символы и шумовые токены.",
            },
            {
                "id": "balanced",
                "label": "Сбалансированный",
                "description": "Рекомендуется по умолчанию: убирает markdown/URL/email и шумовые блоки.",
            },
            {
                "id": "aggressive",
                "label": "Агрессивный",
                "description": "Максимальная очистка служебных и повторяющихся символов для длинных текстов.",
            },
        ],
        "auto_tune": {
            "enabled": True,
            "balanced_threshold": _AUTO_BALANCED_THRESHOLD,
            "aggressive_threshold": _AUTO_AGGRESSIVE_THRESHOLD,
            "hint": (
                "Автонастройка повышает профиль при больших объемах текста, "
                "чтобы движок синтеза меньше проговаривал служебные символы."
            ),
        },
    }


def _apply_auto_tune(settings: EdgeTextNormalizerSettings, text_len: int) -> tuple[EdgeTextNormalizerSettings, bool]:
    if not settings.enabled or not settings.auto_tune:
        return settings, False

    source_preset = _normalize_preset_name(settings.preset)
    target_preset = source_preset

    if text_len >= _AUTO_AGGRESSIVE_THRESHOLD:
        target_preset = "aggressive"
    elif text_len >= _AUTO_BALANCED_THRESHOLD and source_preset == "soft":
        target_preset = "balanced"

    if target_preset == source_preset:
        return settings, False

    profile = _PRESET_OVERRIDES.get(target_preset) or _PRESET_OVERRIDES[_DEFAULT_PRESET]
    tuned = replace(
        settings,
        preset=target_preset,
        strip_markdown=bool(profile["strip_markdown"]),
        unwrap_markdown_links=bool(profile["unwrap_markdown_links"]),
        strip_urls=bool(profile["strip_urls"]),
        strip_emails=bool(profile["strip_emails"]),
        collapse_repeated_symbols=bool(profile["collapse_repeated_symbols"]),
        collapse_repeated_punctuation=bool(profile["collapse_repeated_punctuation"]),
        preserve_ellipsis=bool(profile["preserve_ellipsis"]),
        drop_symbol_only_tokens=bool(profile["drop_symbol_only_tokens"]),
        normalize_whitespace=bool(profile["normalize_whitespace"]),
    )
    return tuned, True


def _remove_control_chars(value: str) -> tuple[str, int]:
    removed = 0
    chunks: list[str] = []
    for char in value:
        if char in ("\n", "\t", "\r"):
            chunks.append(char)
            continue
        category = unicodedata.category(char)
        if category in {"Cc", "Cf"}:
            removed += 1
            continue
        chunks.append(char)
    return "".join(chunks), removed


def _cleanup_whitespace(value: str) -> tuple[str, int]:
    original = value
    text = _NBSP_RE.sub(" ", original)
    lines = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = _WHITESPACE_RE.sub(" ", raw_line).strip()
        lines.append(line)
    text = "\n".join(lines)
    text = _BLANK_LINES_RE.sub("\n\n", text).strip()
    return text, max(0, len(original) - len(text))


def _drop_selected_symbols(value: str, symbols: tuple[str, ...]) -> tuple[str, int]:
    if not symbols:
        return value, 0
    symbol_set = set(symbols)
    removed = sum(1 for ch in value if ch in symbol_set)
    if removed <= 0:
        return value, 0
    chunks: list[str] = []
    for ch in value:
        chunks.append(" " if ch in symbol_set else ch)
    return "".join(chunks), int(removed)


def _collapse_repeated_punctuation(value: str, preserve_ellipsis: bool) -> tuple[str, int]:
    collapsed = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal collapsed
        token = str(match.group(0) or "")
        symbol = str(match.group(1) or "")
        if not token or not symbol:
            return token
        if symbol == "." and preserve_ellipsis and token.count(".") >= 3:
            replacement = "…"
        else:
            replacement = symbol
        if token != replacement:
            collapsed += 1
        return replacement

    normalized = _REPEAT_PUNCT_RE.sub(_replace, value)
    return normalized, int(collapsed)


def normalize_edge_text(text: str, settings: EdgeTextNormalizerSettings | None = None) -> EdgeTextNormalizerResult:
    initial = str(text or "")
    source_settings = settings or EdgeTextNormalizerSettings()
    source_preset = _normalize_preset_name(source_settings.preset)
    effective_settings, auto_tuned = _apply_auto_tune(source_settings, len(initial))
    applied_preset = _normalize_preset_name(effective_settings.preset, source_preset)

    stats: dict[str, int] = {
        "control_chars_removed": 0,
        "urls_removed": 0,
        "emails_removed": 0,
        "markdown_links_unwrapped": 0,
        "markdown_images_unwrapped": 0,
        "markdown_markers_removed": 0,
        "manual_symbols_removed": 0,
        "repeated_symbols_collapsed": 0,
        "repeated_punctuation_collapsed": 0,
        "ellipsis_preserved": 0,
        "symbol_tokens_removed": 0,
        "whitespace_reduced": 0,
        "chars_removed": 0,
    }

    if not effective_settings.enabled:
        summary = "Нормализатор текста отключен."
        return EdgeTextNormalizerResult(
            text=initial,
            settings=effective_settings,
            source_preset=source_preset,
            applied_preset=applied_preset,
            auto_tuned=auto_tuned,
            changed=False,
            stats=stats,
            summary=summary,
        )

    value = unicodedata.normalize("NFKC", initial)
    value, removed_controls = _remove_control_chars(value)
    stats["control_chars_removed"] = int(removed_controls)

    if effective_settings.unwrap_markdown_links:
        value, image_unwrapped = _MARKDOWN_IMAGE_RE.subn(lambda m: str(m.group("label") or "").strip(), value)
        stats["markdown_images_unwrapped"] = int(image_unwrapped)

        value, link_unwrapped = _MARKDOWN_LINK_RE.subn(lambda m: str(m.group("label") or "").strip(), value)
        stats["markdown_links_unwrapped"] = int(link_unwrapped)

    if effective_settings.strip_urls:
        value, urls_removed = _URL_RE.subn(" ", value)
        stats["urls_removed"] = int(urls_removed)

    if effective_settings.strip_emails:
        value, emails_removed = _EMAIL_RE.subn(" ", value)
        stats["emails_removed"] = int(emails_removed)

    if effective_settings.strip_markdown:
        value, fences_removed = _MARKDOWN_FENCE_RE.subn(" ", value)
        value, inline_unwrapped = _MARKDOWN_INLINE_CODE_RE.subn(lambda m: str(m.group(1) or "").strip(), value)
        value, heading_removed = _MARKDOWN_HEADING_RE.subn("", value)
        value, style_removed = _MARKDOWN_STYLE_RE.subn("", value)
        stats["markdown_markers_removed"] = int(fences_removed + inline_unwrapped + heading_removed + style_removed)

    value, manual_symbols_removed = _drop_selected_symbols(value, effective_settings.drop_symbols)
    stats["manual_symbols_removed"] = int(manual_symbols_removed)

    if effective_settings.collapse_repeated_symbols:
        value, collapsed_symbols = _REPEAT_SYMBOL_RE.subn(r"\1", value)
        stats["repeated_symbols_collapsed"] = int(collapsed_symbols)

    if effective_settings.collapse_repeated_punctuation:
        value, collapsed_punct = _collapse_repeated_punctuation(
            value,
            bool(effective_settings.preserve_ellipsis),
        )
        stats["repeated_punctuation_collapsed"] = int(collapsed_punct)
        if effective_settings.preserve_ellipsis:
            stats["ellipsis_preserved"] = len(_ELLIPSIS_TOKEN_RE.findall(value))

    if effective_settings.drop_symbol_only_tokens:
        value, removed_tokens = _SYMBOL_TOKEN_RE.subn(" ", value)
        stats["symbol_tokens_removed"] = int(removed_tokens)

    if effective_settings.normalize_whitespace:
        value, reduced = _cleanup_whitespace(value)
        stats["whitespace_reduced"] = int(reduced)
    else:
        value = value.strip()

    stats["chars_removed"] = max(0, len(initial) - len(value))
    changed = value != initial
    markdown_links_processed = int(stats["markdown_links_unwrapped"] + stats["markdown_images_unwrapped"])
    summary = (
        f"Нормализатор текста ({applied_preset}) изменил текст: удалено символов {stats['chars_removed']}, "
        f"URL {stats['urls_removed']}, markdown-маркеров {stats['markdown_markers_removed']}, "
        f"распаковано markdown-ссылок {markdown_links_processed}, "
        f"шумовых токенов {stats['symbol_tokens_removed']}."
        if changed
        else f"Нормализатор текста ({applied_preset}) не нашел значимых шумовых символов."
    )
    if auto_tuned:
        summary += " Автонастройка усилила профиль для длинного текста."

    return EdgeTextNormalizerResult(
        text=value,
        settings=effective_settings,
        source_preset=source_preset,
        applied_preset=applied_preset,
        auto_tuned=auto_tuned,
        changed=changed,
        stats=stats,
        summary=summary,
    )


def _preview_text(text: str, max_len: int = 420) -> str:
    clean = str(text or "").replace("\r", "").strip()
    if len(clean) <= max_len:
        return clean
    return f"{clean[:max_len - 3]}..."


def _collect_symbol_frequency(text: str) -> list[dict[str, Any]]:
    counter = Counter(ch for ch in str(text or "") if _is_noise_symbol(ch))
    result: list[dict[str, Any]] = []
    for symbol, count in counter.most_common(64):
        result.append({"symbol": symbol, "count": int(count)})
    return result


def analyze_edge_text_readability(text: str, settings: EdgeTextNormalizerSettings | None = None) -> dict[str, Any]:
    source_text = str(text or "")
    result = normalize_edge_text(source_text, settings)
    before_symbols = _collect_symbol_frequency(source_text)
    after_symbols = _collect_symbol_frequency(result.text)
    after_map = {item["symbol"]: int(item["count"]) for item in after_symbols}

    symbol_delta: list[dict[str, Any]] = []
    for item in before_symbols:
        symbol = str(item.get("symbol") or "")
        before_count = int(item.get("count") or 0)
        after_count = int(after_map.get(symbol) or 0)
        symbol_delta.append(
            {
                "symbol": symbol,
                "before": before_count,
                "after": after_count,
                "removed": max(0, before_count - after_count),
            }
        )

    symbol_leftovers = sorted(
        [item for item in symbol_delta if int(item.get("after") or 0) > 0],
        key=lambda item: (-int(item.get("after") or 0), str(item.get("symbol") or "")),
    )
    symbols_total_before = sum(int(item.get("count") or 0) for item in before_symbols)
    symbols_total_after = sum(int(item.get("count") or 0) for item in after_symbols)
    symbols_removed_total = max(0, symbols_total_before - symbols_total_after)

    recommendations: list[str] = []
    if not source_text.strip():
        recommendations.append("Добавьте текст для анализа.")
    elif not result.settings.enabled:
        recommendations.append("Нормализатор отключен. Включите его, чтобы убрать служебные символы.")
    elif result.stats.get("chars_removed", 0) == 0 and symbol_delta:
        recommendations.append("В тексте есть служебные символы. Для более сильной очистки попробуйте профиль aggressive.")
    elif result.stats.get("chars_removed", 0) > 0:
        recommendations.append("Профиль подобран корректно: нормализатор уже убирает шумовые конструкции.")

    if symbol_leftovers:
        if not result.settings.drop_symbol_only_tokens:
            recommendations.append("Включите удаление шумовых токенов, чтобы автоматически очищать служебные символы.")
        else:
            selected_symbols = set(result.settings.drop_symbols or ())
            manual_candidates: list[str] = []
            for item in symbol_leftovers:
                symbol = str(item.get("symbol") or "")
                if not symbol or symbol in selected_symbols:
                    continue
                manual_candidates.append(symbol)
                if len(manual_candidates) >= 6:
                    break
            if manual_candidates:
                recommendations.append(
                    "Остались символы, которые могут звучать в речи. "
                    f"Добавьте в ручную очистку: {', '.join(manual_candidates)}."
                )

    recommendations.append("Перед запуском синтеза проверьте предварительный текст после нормализации.")

    return {
        "input_length": len(source_text),
        "normalized_length": len(result.text),
        "changed": bool(result.changed),
        "summary": result.summary,
        "settings": edge_text_normalizer_settings_payload(result.settings),
        "stats": {key: int(value) for key, value in result.stats.items()},
        "preview": {
            "before": _preview_text(source_text),
            "after": _preview_text(result.text),
        },
        "symbols": {
            "before": before_symbols,
            "after": after_symbols,
            "delta": symbol_delta,
            "leftovers": symbol_leftovers[:16],
            "totals": {
                "before": symbols_total_before,
                "after": symbols_total_after,
                "removed": symbols_removed_total,
                "unique_before": len(before_symbols),
                "unique_after": len(after_symbols),
            },
        },
        "recommendations": recommendations[:6],
    }
