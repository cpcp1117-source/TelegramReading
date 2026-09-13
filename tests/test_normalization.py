from __future__ import annotations

from datetime import UTC, datetime

import pytest

from telegram_trader.normalization import (
    ChannelSymbolPolicy,
    ExchangeSnapshot,
    RawContent,
    classify_media,
    extract_symbol_candidates,
    normalize_message,
    normalize_text,
    resolve_symbol,
)


def _policy(**overrides: object) -> ChannelSymbolPolicy:
    values: dict[str, object] = {
        "channel_id": 1,
        "topic_id": 0,
        "symbol_scope_mode": "STATIC_ALLOWLIST",
        "allowed_symbols": frozenset({"BTCUSDT", "ETHUSDT"}),
        "prohibited_symbols": frozenset(),
    }
    values.update(overrides)
    return ChannelSymbolPolicy(**values)  # type: ignore[arg-type]


def test_normalize_text_collapses_whitespace_and_trims() -> None:
    raw = "  Long \t entry   here  \n\n\n\nSL: 100\n"

    normalized = normalize_text(raw, "text")

    assert normalized == "Long entry here\n\nSL: 100"


def test_normalize_text_applies_unicode_nfkc_normalization() -> None:
    # Fullwidth Latin letters (U+FF21-U+FF23) collapse to ASCII "ABC" under NFKC.
    normalized = normalize_text("ＡＢＣ", "text")  # noqa: RUF001

    assert normalized == "ABC"


def test_normalize_text_strips_zero_width_characters() -> None:
    normalized = normalize_text("BTC​USDT", "caption")

    assert normalized == "BTCUSDT"


@pytest.mark.parametrize("content_type", ["image", "empty"])
def test_normalize_text_is_empty_for_non_text_content(content_type: str) -> None:
    assert normalize_text("ignored", content_type) == ""


def test_normalize_text_handles_none_input() -> None:
    assert normalize_text(None, "text") == ""


def test_extract_symbol_candidates_finds_uppercase_tokens_and_dollar_prefix() -> None:
    candidates = extract_symbol_candidates("Long $BTC entry, target ETHUSDT, avoid the SCAM coin")

    assert candidates == ["BTC", "ETHUSDT", "SCAM"]


def test_extract_symbol_candidates_ignores_lowercase_and_pure_digits() -> None:
    candidates = extract_symbol_candidates("buy low sell high 12345")

    assert candidates == []


def test_extract_symbol_candidates_deduplicates_preserving_order() -> None:
    candidates = extract_symbol_candidates("BTCUSDT then BTCUSDT again then ETHUSDT")

    assert candidates == ["BTCUSDT", "ETHUSDT"]


def test_resolve_symbol_static_allowlist_valid() -> None:
    decision = resolve_symbol("BTCUSDT", _policy())

    assert decision.symbol == "BTCUSDT"
    assert decision.status == "VALID"


def test_resolve_symbol_static_allowlist_invalid() -> None:
    decision = resolve_symbol("DOGEUSDT", _policy())

    assert decision.status == "INVALID"


def test_resolve_symbol_prohibited_overrides_allowlist() -> None:
    policy = _policy(
        allowed_symbols=frozenset({"BTCUSDT"}), prohibited_symbols=frozenset({"BTCUSDT"})
    )

    decision = resolve_symbol("BTCUSDT", policy)

    assert decision.status == "INVALID"


def test_resolve_symbol_static_allowlist_resolves_bare_asset_via_quote_currency_alias() -> None:
    """Real channel data: authors write "BTC"/"ETH", the allowlist stores "BTCUSDT"/"ETHUSDT"."""
    decision = resolve_symbol("BTC", _policy(allowed_symbols=frozenset({"BTCUSDT"})))

    assert decision.symbol == "BTCUSDT"
    assert decision.status == "VALID"


def test_resolve_symbol_static_allowlist_alias_still_invalid_when_no_pair_matches() -> None:
    decision = resolve_symbol("DOGE", _policy(allowed_symbols=frozenset({"BTCUSDT"})))

    assert decision.status == "INVALID"


def test_resolve_symbol_prohibited_blocks_bare_asset_alias_too() -> None:
    policy = _policy(
        allowed_symbols=frozenset({"BTCUSDT"}), prohibited_symbols=frozenset({"BTCUSDT"})
    )

    decision = resolve_symbol("BTC", policy)

    assert decision.status == "INVALID"


def test_resolve_symbol_binance_dynamic_scope_pending_without_snapshot() -> None:
    """No usable data yet (none fetched, or the caller decided it's stale) -> fail closed."""
    policy = _policy(symbol_scope_mode="BINANCE_USDM_ACTIVE_PERPETUAL", allowed_symbols=frozenset())

    decision = resolve_symbol("BTCUSDT", policy, snapshot=None)

    assert decision.status == "PENDING_MARKET_DATA"


def test_resolve_symbol_binance_dynamic_scope_still_respects_prohibited() -> None:
    policy = _policy(
        symbol_scope_mode="BINANCE_USDM_ACTIVE_PERPETUAL",
        allowed_symbols=frozenset(),
        prohibited_symbols=frozenset({"LUNAUSDT"}),
    )

    decision = resolve_symbol("LUNAUSDT", policy)

    assert decision.status == "INVALID"


def _snapshot(active_symbols: frozenset[str]) -> ExchangeSnapshot:
    return ExchangeSnapshot(
        fetched_at=datetime(2026, 9, 13, tzinfo=UTC), active_symbols=active_symbols
    )


def test_resolve_symbol_binance_dynamic_scope_valid_with_matching_snapshot() -> None:
    policy = _policy(symbol_scope_mode="BINANCE_USDM_ACTIVE_PERPETUAL", allowed_symbols=frozenset())

    decision = resolve_symbol(
        "BTCUSDT", policy, snapshot=_snapshot(frozenset({"BTCUSDT", "ETHUSDT"}))
    )

    assert decision.symbol == "BTCUSDT"
    assert decision.status == "VALID"


def test_resolve_symbol_binance_dynamic_scope_resolves_bare_asset_via_alias() -> None:
    policy = _policy(symbol_scope_mode="BINANCE_USDM_ACTIVE_PERPETUAL", allowed_symbols=frozenset())

    decision = resolve_symbol("BTC", policy, snapshot=_snapshot(frozenset({"BTCUSDT"})))

    assert decision.symbol == "BTCUSDT"
    assert decision.status == "VALID"


def test_resolve_symbol_binance_dynamic_scope_invalid_when_snapshot_has_no_match() -> None:
    policy = _policy(symbol_scope_mode="BINANCE_USDM_ACTIVE_PERPETUAL", allowed_symbols=frozenset())

    decision = resolve_symbol("DOGE", policy, snapshot=_snapshot(frozenset({"BTCUSDT"})))

    assert decision.status == "INVALID"


def test_resolve_symbol_binance_dynamic_scope_invalid_when_ambiguous() -> None:
    """A candidate matching both a USDT and a USDC perpetual isn't uniquely mapped (FR-002)."""
    policy = _policy(symbol_scope_mode="BINANCE_USDM_ACTIVE_PERPETUAL", allowed_symbols=frozenset())

    decision = resolve_symbol("BTC", policy, snapshot=_snapshot(frozenset({"BTCUSDT", "BTCUSDC"})))

    assert decision.status == "INVALID"


def test_resolve_symbol_binance_dynamic_scope_prohibited_overrides_snapshot_match() -> None:
    policy = _policy(
        symbol_scope_mode="BINANCE_USDM_ACTIVE_PERPETUAL",
        allowed_symbols=frozenset(),
        prohibited_symbols=frozenset({"BTCUSDT"}),
    )

    decision = resolve_symbol("BTCUSDT", policy, snapshot=_snapshot(frozenset({"BTCUSDT"})))

    assert decision.status == "INVALID"


def test_resolve_symbol_rejects_unknown_scope_mode() -> None:
    policy = _policy(symbol_scope_mode="NOT_A_MODE")

    with pytest.raises(ValueError, match="unknown symbol_scope_mode"):
        resolve_symbol("BTCUSDT", policy)


def test_classify_media_flags_manual_review_only_when_present() -> None:
    assert classify_media(None) == "NOT_APPLICABLE"
    assert classify_media("a" * 64) == "PENDING_MANUAL_REVIEW"


def _raw(**overrides: object) -> RawContent:
    values: dict[str, object] = {
        "raw_message_id": "raw-1",
        "channel_id": 1,
        "topic_id": None,
        "text": "Long BTCUSDT entry 60000",
        "content_type": "text",
        "media_sha256": None,
    }
    values.update(overrides)
    return RawContent(**values)  # type: ignore[arg-type]


def test_normalize_message_is_deterministic_across_repeated_calls() -> None:
    first = normalize_message(_raw(), _policy())
    second = normalize_message(_raw(), _policy())

    assert first == second
    assert first.content_id == second.content_id
    assert first.content_hash == second.content_hash


def test_normalize_message_content_id_changes_with_version() -> None:
    v1 = normalize_message(_raw(), _policy(), version="v1")
    v2 = normalize_message(_raw(), _policy(), version="v2")

    assert v1.content_id != v2.content_id
    assert v1.normalizer_version == "v1"
    assert v2.normalizer_version == "v2"


def test_normalize_message_flags_media_for_manual_review() -> None:
    result = normalize_message(
        _raw(content_type="image", text=None, media_sha256="a" * 64), _policy()
    )

    assert result.media_review_status == "PENDING_MANUAL_REVIEW"
    assert result.normalized_text == ""


def test_normalize_message_resolves_symbols_against_policy() -> None:
    result = normalize_message(
        _raw(text="Long BTCUSDT, avoid DOGEUSDT"),
        _policy(allowed_symbols=frozenset({"BTCUSDT"})),
    )

    resolved = {r.symbol: r.status for r in result.resolved_symbols}
    assert resolved == {"BTCUSDT": "VALID", "DOGEUSDT": "INVALID"}


def test_normalize_message_threads_snapshot_into_dynamic_scope_resolution() -> None:
    result = normalize_message(
        _raw(text="Long BTC now"),
        _policy(symbol_scope_mode="BINANCE_USDM_ACTIVE_PERPETUAL", allowed_symbols=frozenset()),
        snapshot=_snapshot(frozenset({"BTCUSDT"})),
    )

    resolved = {r.symbol: r.status for r in result.resolved_symbols}
    assert resolved == {"BTCUSDT": "VALID"}
