# Phase 2 Requirement Traceability

- **Source:** [phase-kickoff.md](phase-kickoff.md) In Scope list and Gate 2 Required Evidence
- **Fixed Point:** `f4ccff6a039f42df35fc81054c9208861b42ecd3`

| ID | Requirement | Implementation | Verification | Status |
|---|---|---|---|---|
| P2-REQ-001 | Login/bootstrap credentials handled locally only | `telegram-bootstrap.ps1`, `telegram_readonly.py`, `config.py` | manual local run; secret scan 0 findings | PASS |
| P2-REQ-002 | Channel dialog listing restricted to allowlisted target | `telegram_cli.py` `dialog_listing_result` | `dialogs` command run 2026-09-06; `target_found=true` | PASS |
| P2-REQ-003 | `NewMessage` handling | `TelethonReadOnlyCollector.run_connection` (`events.NewMessage`) | fake-client unit tests in `test_telegram_collector.py` | PASS (unit); **live NEW event not yet observed** |
| P2-REQ-004 | `EditedMessage` handling and version retention | `TelethonReadOnlyCollector.run_connection` (`events.MessageEdited`), `TelegramMessageProcessor` | `test_content_fingerprint_changes_for_edit` (storage layer, fake data) | PARTIAL — unit-covered only; no live edit observed |
| P2-REQ-005 | Reply/forward relationship preserved | `telegram_message_input` (`reply_to_message_id`, `forward_*` fields) | schema constraints in `models.py`; fake-client tests | PASS (unit); live reply/forward sample not manually spot-checked |
| P2-REQ-006 | Text and caption extraction | `telegram_message_input` content-type branching | live backfill: 150 text + 209 caption rows confirmed correct by manual spot-check | PASS |
| P2-REQ-007 | Image download and media hash | `MediaStore`, `telegram_message_input` photo branch | live backfill: 108 `image` rows persisted; `test_media_store_hashes_and_uses_safe_extension` | PASS |
| P2-REQ-008 | History backfill with overlap on resume | `TethonReadOnlyCollector.backfill`, `backfill_overlap=100` | live Run 2 restart: `backfill_seen=95` re-scan, 0 new duplicate rows | PASS |
| P2-REQ-009 | Reconnect and retry with capped backoff | `reconnect_delay` (exponential, capped 60s) | `test_reconnect_delay_is_exponential_and_capped` | PASS (unit); live reconnect-after-network-drop not exercised |
| P2-REQ-010 | Dedup by `(channel_id, message_id, edit_version)` | `uq_telegram_message_version` unique constraint (`models.py`) | live: `COUNT(*)=COUNT(DISTINCT message_id)=500`; 0 duplicate groups after restart | PASS |
| P2-REQ-011 | Checkpoint persists and drives controlled-restart recovery | `TelegramCollectorCheckpoint`, `TelegramMessageSink.checkpoint()` | live: checkpoint stable at `6655` across restart; backfill min_id computed from checkpoint | PASS (restart-no-duplicate proven); **restart-no-loss not yet proven** (no new messages arrived to test against) |
| P2-REQ-012 | Read-only audit trail / operation status check | structured JSON logs (`telegram collector ready`, disconnect/retry warnings) | manual log review; `security-check.md` | PASS |
| P2-REQ-013 | Only reads channels the account can legally access | `resolve_target` allowlist check (`expected_channel_id`) | `dialogs` output shows single target channel; DB shows single `channel_id` | PASS |
| P2-REQ-014 | Telegram session never exposed in log/Git/reports | `.gitignore`, log field review | [security-check.md](security-check.md) | PASS |
| P2-REQ-015 | 24-hour continuous read-only operation | n/a | longest run so far ~21 minutes | **PENDING** |

## Acceptance Criteria

| AC | Given / When / Then | Evidence | Status |
|---|---|---|---|
| P2-AC-001 | Given the collector starts, when backfill completes, then a `telegram collector ready` log with `backfill_seen`/`checkpoint` is emitted | live Run 1 and Run 2 JSON lines | PASS |
| P2-AC-002 | Given a controlled restart, when the collector resumes, then no duplicate `(channel_id, message_id, edit_version)` rows are created | live restart, 0 duplicate groups | PASS |
| P2-AC-003 | Given a controlled restart, when new messages arrive during the gap, then none are missed | not yet observable — channel was quiet between Run 1 and Run 2 | **PENDING** |
| P2-AC-004 | Given a message is edited on the channel, when the collector observes the edit, then both the original and edited versions persist | no live edit occurred in the observed window | **PENDING** |
| P2-AC-005 | Given the collector runs continuously, when 24 hours elapse, then no unhandled crash occurs and read-only invariants hold | soak not yet started | **PENDING** |
| P2-AC-006 | Given collected data, when compared to `@followgerry` manually, then message IDs and counts match | 20/500 spot-checked, matched | PARTIAL |
| P2-AC-007 | Given repository and runtime artifacts, when scanned for secrets, then findings are zero and no session data leaks | secret scan 0 findings; log/Git review clean | PASS |

Overall status: **Phase 2 functional path implemented and partially live-validated; Gate 2 evidence incomplete** — see [gate-2-checklist.md](gate-2-checklist.md).
