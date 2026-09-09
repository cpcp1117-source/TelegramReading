from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

CURRENT_NORMALIZER_VERSION = "v1"

SymbolStatus = Literal["VALID", "INVALID", "PENDING_MARKET_DATA"]
MediaReviewStatus = Literal["NOT_APPLICABLE", "PENDING_MANUAL_REVIEW"]

_ZERO_WIDTH_TRANSLATION = str.maketrans("", "", "​‌‍﻿")
_WHITESPACE_RUN = re.compile(r"[ \t]+")
_BLANK_LINE_RUN = re.compile(r"\n{3,}")
_SYMBOL_CANDIDATE_PATTERN = re.compile(r"\$?\b[A-Z0-9]{2,15}\b")


@dataclass(frozen=True, slots=True)
class RawContent:
    """The subset of a `telegram_message_versions` row normalization needs."""

    raw_message_id: str
    channel_id: int
    topic_id: int | None
    text: str | None
    content_type: str
    media_sha256: str | None


@dataclass(frozen=True, slots=True)
class ChannelSymbolPolicy:
    """A narrow, symbol-focused view of one `channel_policies` row.

    Deliberately separate from `channel_policy.ChannelPolicySnapshot`, which
    is narrowed for raw-collection gating instead -- same reasoning: each
    consumer gets its own minimal view of the shared table.
    """

    channel_id: int
    topic_id: int
    symbol_scope_mode: str
    allowed_symbols: frozenset[str]
    prohibited_symbols: frozenset[str]


@dataclass(frozen=True, slots=True)
class ResolvedSymbol:
    symbol: str
    status: SymbolStatus


@dataclass(frozen=True, slots=True)
class NormalizedContentResult:
    """Pure output of `normalize_message`; the batch job maps this onto the

    `normalized_content` ORM row (`models.NormalizedContent`).
    """

    content_id: str
    raw_message_id: str
    channel_id: int
    topic_id: int | None
    normalizer_version: str
    normalized_text: str
    symbol_scope_mode: str
    symbol_candidates: list[str]
    resolved_symbols: list[ResolvedSymbol]
    media_review_status: MediaReviewStatus
    content_hash: str


def normalize_text(raw_text: str | None, content_type: str) -> str:
    """Deterministic text/caption cleanup, preserving meaning while making

    downstream comparison/hashing stable: Unicode NFKC normalization, zero-
    width character removal, trimmed lines, collapsed horizontal whitespace
    and excess blank lines. Image/empty content normalizes to "".
    """
    if raw_text is None or content_type in ("image", "empty"):
        return ""
    text = unicodedata.normalize("NFKC", raw_text)
    text = text.translate(_ZERO_WIDTH_TRANSLATION)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = _WHITESPACE_RUN.sub(" ", text)
    text = _BLANK_LINE_RUN.sub("\n\n", text)
    return text.strip()


def extract_symbol_candidates(normalized_text: str) -> list[str]:
    """Deterministic, order-preserving extraction of ticker-shaped tokens.

    Intentionally simple pattern matching (uppercase/digit tokens, optional
    leading `$`), not the structured EXECUTION_SIGNAL parser -- that is a
    later Phase 4 slice. This only feeds symbol-scope classification.
    """
    candidates: dict[str, None] = {}
    for match in _SYMBOL_CANDIDATE_PATTERN.finditer(normalized_text):
        candidate = match.group(0).lstrip("$")
        if candidate.isdigit():
            continue
        candidates.setdefault(candidate, None)
    return list(candidates)


_QUOTE_CURRENCY_SUFFIXES = ("USDT", "USD", "BUSD")


def _candidate_aliases(candidate: str) -> tuple[str, ...]:
    """A bare base-asset shorthand (e.g. "BTC") plus its common USD-quoted pair spellings.

    Real channel data shows authors routinely write the bare asset name
    ("BTC", "ETH") even when the channel's allowlist is expressed as a
    quoted pair ("BTCUSDT"). This only widens what counts as a *match* for
    `STATIC_ALLOWLIST`; it never invents a pair that isn't already on the
    channel's declared allowlist.
    """
    return (candidate, *(candidate + suffix for suffix in _QUOTE_CURRENCY_SUFFIXES))


def resolve_symbol(candidate: str, policy: ChannelSymbolPolicy) -> ResolvedSymbol:
    """Resolve one candidate against its channel's declared symbol scope.

    `BINANCE_USDM_ACTIVE_PERPETUAL` always resolves to `PENDING_MARKET_DATA`
    in Phase 4: this phase must not call any Binance endpoint (not even
    public market data -- that starts Phase 5), so a dynamic-scope symbol
    cannot be confirmed as an active perpetual yet, regardless of whether it
    is spelled as a bare asset or a full pair. Marking it explicitly
    unresolved (rather than guessing VALID) keeps the system fail-closed.
    """
    if policy.symbol_scope_mode == "BINANCE_USDM_ACTIVE_PERPETUAL":
        if candidate in policy.prohibited_symbols:
            return ResolvedSymbol(candidate, "INVALID")
        return ResolvedSymbol(candidate, "PENDING_MARKET_DATA")
    if policy.symbol_scope_mode == "STATIC_ALLOWLIST":
        aliases = _candidate_aliases(candidate)
        if any(alias in policy.prohibited_symbols for alias in aliases):
            return ResolvedSymbol(candidate, "INVALID")
        for alias in aliases:
            if alias in policy.allowed_symbols:
                return ResolvedSymbol(alias, "VALID")
        return ResolvedSymbol(candidate, "INVALID")
    raise ValueError(f"unknown symbol_scope_mode: {policy.symbol_scope_mode!r}")


def classify_media(media_sha256: str | None) -> MediaReviewStatus:
    """BR-003: image-derived instructions always go to a human, never auto-processed.

    Phase 4 has no OCR/Vision engine, so this is a flag only.
    """
    return "PENDING_MANUAL_REVIEW" if media_sha256 is not None else "NOT_APPLICABLE"


def _content_id(raw_message_id: str, version: str) -> str:
    return hashlib.sha256(f"{raw_message_id}:{version}".encode()).hexdigest()


def normalize_message(
    raw: RawContent,
    policy: ChannelSymbolPolicy,
    *,
    version: str = CURRENT_NORMALIZER_VERSION,
) -> NormalizedContentResult:
    """Deterministic composition: same `raw` + `policy` + `version` always

    produces byte-identical output (NFR-010), since every step above is a
    pure function over its inputs.
    """
    normalized_text = normalize_text(raw.text, raw.content_type)
    candidates = extract_symbol_candidates(normalized_text)
    resolved = [resolve_symbol(candidate, policy) for candidate in candidates]
    media_review_status = classify_media(raw.media_sha256)

    canonical = {
        "raw_message_id": raw.raw_message_id,
        "normalizer_version": version,
        "normalized_text": normalized_text,
        "symbol_scope_mode": policy.symbol_scope_mode,
        "symbol_candidates": candidates,
        "resolved_symbols": [{"symbol": r.symbol, "status": r.status} for r in resolved],
        "media_review_status": media_review_status,
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    content_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    return NormalizedContentResult(
        content_id=_content_id(raw.raw_message_id, version),
        raw_message_id=raw.raw_message_id,
        channel_id=raw.channel_id,
        topic_id=raw.topic_id,
        normalizer_version=version,
        normalized_text=normalized_text,
        symbol_scope_mode=policy.symbol_scope_mode,
        symbol_candidates=candidates,
        resolved_symbols=resolved,
        media_review_status=media_review_status,
        content_hash=content_hash,
    )
