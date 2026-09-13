from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

# Bumped v3 -> v4: the real backlog's v3 pass ran before binance_symbol_snapshots
# had any row in it yet (an operational sequencing mistake -- migration 0011 was
# applied after, not before, the v3 run), so the entire backlog is stuck at
# PENDING_MARKET_DATA under v3 with no way to revisit it (normalized_content is
# append-only per version). This bump forces one more full reprocess, this time
# with a real snapshot already in place.
CURRENT_NORMALIZER_VERSION = "v4"

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
class ExchangeSnapshot:
    """A pure, already-staleness-decided view of the latest `binance_symbol_snapshots` row.

    `binance_market_data.load_latest_snapshot` is the only place that reads
    a clock or a staleness threshold; by the time a snapshot reaches
    `resolve_symbol`, it is either `None` ("no usable data right now, for
    any reason") or fresh. This keeps normalization free of any wall-clock
    dependency, preserving the NFR-010 determinism contract.
    """

    fetched_at: datetime
    active_symbols: frozenset[str]


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

# Distinct from _QUOTE_CURRENCY_SUFFIXES: that tuple was built for
# STATIC_ALLOWLIST's curated fixtures and is left untouched (already
# live-verified). Real Binance USDⓈ-M exchangeInfo data no longer has BUSD-
# margined perpetuals (delisted Dec 2023) but does have live USDC-margined
# perpetuals alongside USDT ones (e.g. BTCUSDT and BTCUSDC can both be
# active) -- the one realistic case where a candidate maps to more than one
# active perpetual.
_DYNAMIC_SCOPE_QUOTE_SUFFIXES = ("USDT", "USDC")


def _candidate_aliases(
    candidate: str, *, suffixes: tuple[str, ...] = _QUOTE_CURRENCY_SUFFIXES
) -> tuple[str, ...]:
    """A bare base-asset shorthand (e.g. "BTC") plus its common quoted-pair spellings.

    Real channel data shows authors routinely write the bare asset name
    ("BTC", "ETH") even when the channel's allowlist is expressed as a
    quoted pair ("BTCUSDT"). This only widens what counts as a *match*; it
    never invents a pair that isn't already present in whatever the caller
    is matching against (an allowlist, or a live exchange snapshot).
    """
    return (candidate, *(candidate + suffix for suffix in suffixes))


def resolve_symbol(
    candidate: str,
    policy: ChannelSymbolPolicy,
    *,
    snapshot: ExchangeSnapshot | None = None,
) -> ResolvedSymbol:
    """Resolve one candidate against its channel's declared symbol scope.

    `BINANCE_USDM_ACTIVE_PERPETUAL` resolves against `snapshot` when one is
    available: `snapshot=None` means "no usable exchange data right now",
    for any reason (none fetched yet, or the caller's own staleness check
    rejected it -- see `ExchangeSnapshot`), and keeps the prior Phase 4
    fail-closed behavior of `PENDING_MARKET_DATA` rather than guessing
    VALID. When a snapshot is available, a candidate matching exactly one
    active perpetual (bare or aliased) resolves VALID; zero or more than
    one match (not uniquely mapped, per FR-002) resolves INVALID.
    """
    if policy.symbol_scope_mode == "BINANCE_USDM_ACTIVE_PERPETUAL":
        if candidate in policy.prohibited_symbols:
            return ResolvedSymbol(candidate, "INVALID")
        if snapshot is None:
            return ResolvedSymbol(candidate, "PENDING_MARKET_DATA")
        aliases = _candidate_aliases(candidate, suffixes=_DYNAMIC_SCOPE_QUOTE_SUFFIXES)
        matches = [alias for alias in aliases if alias in snapshot.active_symbols]
        if len(matches) == 1:
            return ResolvedSymbol(matches[0], "VALID")
        return ResolvedSymbol(candidate, "INVALID")
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
    snapshot: ExchangeSnapshot | None = None,
) -> NormalizedContentResult:
    """Deterministic composition: same `raw` + `policy` + `version` + `snapshot` always

    produces byte-identical output (NFR-010), since every step above is a
    pure function over its inputs -- `snapshot` is an explicit, versioned
    input the same way `policy` already is, not a live lookup.
    """
    normalized_text = normalize_text(raw.text, raw.content_type)
    candidates = extract_symbol_candidates(normalized_text)
    resolved = [resolve_symbol(candidate, policy, snapshot=snapshot) for candidate in candidates]
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
