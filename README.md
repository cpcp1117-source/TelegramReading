# Telegram Channel Trading Monitor

本專案採嚴格 Sequential Stage Gate。任何 Phase 未取得 `READY + User Accepted` 前，不得開始下一 Phase。

## Current Status

- Completed phases: `Phase 0 — Requirements and Architecture`; `Phase 1 — Offline Foundation`
- Active phase: `Phase 2 — Telegram Read-only Collector` (authorized; branch creation pending)
- Gate status: `Phase 0 READY + USER_ACCEPTED`; `Phase 1 READY + USER_ACCEPTED`
- Runtime code: Phase 1 offline-only skeleton exists
- External credentials: 不需要，且不得加入工作區
- Git branch: `phase/1-offline-foundation`
- Git remote: `https://github.com/cpcp1117-source/TelegramReading.git`
- Initial channel scope: only `@followgerry`; future channels require separate onboarding after the current system is stable
- Next permitted action: merge Phase 1, create `phase-1-accepted`, then create the Phase 2 branch

## Phase 0 Deliverables

- [System Specification](docs/phase-0/system-spec.md)
- [Architecture and Data Flow](docs/phase-0/architecture.md)
- [Logical Data Model](docs/phase-0/logical-data-model.md)
- [Channel Onboarding Template](docs/phase-0/channel-onboarding-template.md)
- [Monster-貨幣宇宙中心 Onboarding Record](docs/phase-0/channels/monster-currency-universe.md)
- [Credential Handoff Procedure](docs/phase-0/credential-handoff.md)
- [Threat Model](docs/phase-0/threat-model.md)
- [Test Strategy](docs/phase-0/test-strategy.md)
- [Acceptance Traceability Matrix](docs/phase-0/acceptance-traceability.md)
- [Telegram / Binance API Contract Inventory](docs/phase-0/api-contract-inventory.md)
- [Phase Report](docs/phase-0/phase-report.md)
- [Gate 0 Delivery Quality Review](docs/phase-0/quality-review.md)
- [Gate 0 Checklist](docs/phase-0/gate-0-checklist.md)
- [Domain Context](docs/phase-0/CONTEXT.md)
- [Architecture Decision Records](docs/adr/)

## Gate Rule

Gate 0 re-review 為 `READY`，使用者已於 2026-09-03 明確批准 Specification v0.2。Phase 1 必須在獨立 branch 僅開發 Offline Foundation；Telegram、Binance、OpenAI 與真實交易能力仍禁止。

## Phase 1 — Offline Foundation

Active branch: `phase/1-offline-foundation`

Phase 1 contains only an offline application skeleton:

- Python 3.11 package with offline-only config validation.
- PostgreSQL schema and Alembic migration.
- Append-only audit events protected by a database trigger.
- Deterministic mock Telegram event simulator and persisted checkpoints.
- JSON structured logging with sensitive-key redaction.
- HTTP liveness/readiness endpoints.
- Docker Compose with an internal-only database network.
- Ruff、mypy、pytest、coverage and repository secret scan commands.

No Telegram、Binance or OpenAI SDK/client exists in this phase.

### Local Static and Unit Checks

```powershell
.\scripts\ci.ps1
```

### Docker Compose

```powershell
$dbCredential = ([Guid]::NewGuid().ToString("N")) + "@:/#%?[]!+"
Set-Item -Path Env:POSTGRES_PASSWORD -Value $dbCredential
docker compose up --build -d
docker compose ps
Invoke-RestMethod http://127.0.0.1:8080/health/ready
```

`POSTGRES_PASSWORD` 只存在目前 terminal process；不得寫入 repository 或測試報告。App 以分欄設定建立 SQLAlchemy URL，密碼可包含 URL 特殊字元。

PostgreSQL 只會在第一次建立 volume 時套用 `POSTGRES_PASSWORD`。若既有 volume 需要改密碼，必須在資料庫內輪替；只修改環境變數會造成驗證失敗。僅在確認本機測試資料可刪除時，才可使用 `docker compose down --volumes`重建。

### PostgreSQL Integration Tests

```powershell
docker compose --profile test run --rm test alembic upgrade head
docker compose --profile test run --rm test pytest --cov --cov-report=term-missing
```

Gate 1 remains unaccepted until all required evidence is recorded, the review verdict is `READY`, and the user explicitly accepts it.

### Gate 1 Acceptance Package

- [Phase Report](docs/phase-1/phase-report.md)
- [Test Evidence](docs/phase-1/test-evidence.md)
- [Requirement Traceability](docs/phase-1/requirement-traceability.md)
- [Security Check](docs/phase-1/security-check.md)
- [Known Issues](docs/phase-1/known-issues.md)
- [Delivery Quality Review](docs/phase-1/quality-review.md)
- [Gate 1 Checklist](docs/phase-1/gate-1-checklist.md)
