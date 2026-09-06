# Telegram Channel Trading Monitor

本專案採嚴格 Sequential Stage Gate。任何 Phase 未取得 `READY + User Accepted` 前，不得開始下一 Phase。

## Current Status

- Completed phases: `Phase 0 — Requirements and Architecture`; `Phase 1 — Offline Foundation`
- Active phase: `Phase 2 — Telegram Read-only Collector` (IN PROGRESS)
- Gate status: `Phase 0 READY + USER_ACCEPTED`; `Phase 1 READY + USER_ACCEPTED`; `Gate 2 NOT EVALUATED`
- Runtime code: Phase 1 offline-only skeleton exists
- External credentials: Phase 2 僅允許透過本機 terminal 引入 Telegram credentials；不得加入工作區、Git、log 或報告
- Git branch: `phase/2-telegram-readonly-collector`
- Git remote: `https://github.com/cpcp1117-source/TelegramReading.git`
- Initial channel scope: only `@followgerry`; future channels require separate onboarding after the current system is stable
- Next permitted action: implement and verify only the Phase 2 read-only Telegram collector scope

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

Gate 0 re-review 為 `READY`，使用者已於 2026-09-03 明確批准 Specification v0.2。Phase 1 已於 2026-09-06 取得使用者驗收、合併至 `main`，並建立 `phase-1-accepted` tag。Phase 2 僅可開發 Telegram read-only collector；Binance、OpenAI、訊號解析與真實交易能力仍禁止。

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

Gate 1 was explicitly accepted by the user on 2026-09-06 and is preserved by the `phase-1-accepted` tag.

### Gate 1 Acceptance Package

- [Phase Report](docs/phase-1/phase-report.md)
- [Test Evidence](docs/phase-1/test-evidence.md)
- [Requirement Traceability](docs/phase-1/requirement-traceability.md)
- [Security Check](docs/phase-1/security-check.md)
- [Known Issues](docs/phase-1/known-issues.md)
- [Delivery Quality Review](docs/phase-1/quality-review.md)
- [Gate 1 Checklist](docs/phase-1/gate-1-checklist.md)

## Phase 2 — Telegram Read-only Collector

Active branch: `phase/2-telegram-readonly-collector`

Phase 2 開工範圍、禁止事項、憑證規則與 Gate 2 驗證條件記錄於 [Phase 2 Kickoff](docs/phase-2/phase-kickoff.md)。本階段只處理初始頻道 `@followgerry`，尚未引入任何 Telegram credential。

### Phase 2 Safe Telegram Bootstrap

先執行完整 CI 建立隔離的 `.venv-ci`：

```powershell
.\scripts\ci.ps1
```

再於本機 terminal 互動登入。腳本會隱藏 API Hash，並在結束時移除 process environment credentials：

```powershell
.\scripts\telegram-bootstrap.ps1 -Command login
.\scripts\telegram-bootstrap.ps1 -Command dialogs
```

手機號碼、Telegram 驗證碼與 2FA 只輸入 terminal。請勿貼到聊天、`.env`、GitHub issue、log 或測試報告。Session 只會保存在 Git ignored 的 `secrets/telegram/`。
