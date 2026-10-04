# Channel Onboarding Record — 邦妮區塊鏈 VIP專屬CLUB (幣種分析)

Enforced values live in the `channel_policies` table (row `channel_id=3182714504, topic_id=7`, seeded by `alembic/versions/0021_analysis_setups.py`). This file is the human-readable record; **if the two disagree, the table governs runtime behavior**.

## Identity

| Field | Value |
|---|---|
| Channel | 邦妮區塊鏈｜VIP專屬CLUB（private channel, no public username） |
| Numeric channel ID | `3182714504` |
| Topic ID | `7`（幣種分析討論串；topic 8 是聊天室，**不收**） |
| Channel type | `ANALYSIS` |
| Symbol scope | `BINANCE_USDM_ACTIVE_PERPETUAL`（all active USD-M perpetuals） |
| Gate decision | `MONITOR_ONLY` |

## Authorization (self-declared, **please confirm**)

Recorded 2026-10-04 on the user's instruction to collect this channel. The collector account is a member of the channel. All four scopes below were set to `GRANTED` by the migration on that basis; they are the user's own declaration, not a legal review. **Edit this table and the `channel_policies` row if any of it is wrong.**

| Scope | Status |
|---|---|
| Account may access content | GRANTED |
| Automated collection/monitoring | GRANTED |
| AI processing (text only, to the configured AI provider) | GRANTED |
| Image/media storage | GRANTED |

Revocation: if membership ends or the channel forbids automated collection / AI processing, set the matching `channel_policies` column to `REVOKED`; the pipeline fails closed.

## Content shape (observed 2026-10-03)

Each analysis post is a photo album: the first item carries the caption, the rest are empty. Posts are either a full setup (`DOT 空 / EN：1.18-1.2 / SL：1.23上方 / TP：…`) or a short update that does **not** name the coin ("小級別跌破1.18…止損建議設在1.23以上"), so the AI step reads each message together with the previous messages of the topic. Charts are not sent to the AI (text only, BR-003).

## How a post becomes a draft

See `docs/proposals/analysis-channels-handoff.md` §7–8: AI describes the setup → code watches Binance 15m candles → stage 1 "price touched the entry zone" → stage 2 candle-close confirmation → pre-filled draft sent through the normal approval flow. Nothing is ordered without the user's approval.
