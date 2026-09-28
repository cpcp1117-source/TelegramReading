# Telegram Channel Trading Monitor

本專案採嚴格 Sequential Stage Gate。任何 Phase 未取得 `READY + User Accepted` 前，不得開始下一 Phase。

## Current Status

- Completed phases: `Phase 0 — Requirements and Architecture`; `Phase 1 — Offline Foundation`; `Phase 2 — Telegram Read-only Collector`; `Phase 3 — Channel Policy Enforcement`; `Phase 4 — Signal Lifecycle / Control Bot`; `Phase 5 — AI Analysis / Public Market Data` (risk-accepted; AI/ANALYSIS path paused, see [gate-5-checklist.md](docs/phase-5/gate-5-checklist.md))
- Active phase: `Phase 6` (KICKOFF — not yet started)
- Gate status: Phases 0–5 all `READY + USER_ACCEPTED`; `Gate 6 NOT EVALUATED`
- Runtime code: Telegram read-only collector, Channel Policy enforcement, normalization/signal-parsing pipeline, Control Bot (approve/reject/edit SL-TP), Binance public market-data resolution, Thesis extraction (code-complete, not yet run against real data — AI path paused)
- `collector`/`control-bot` run continuously (`restart: unless-stopped`) against the real `@followgerry` and bonnie-blockchain channels
- External credentials: Telegram (collector + Control Bot), PostgreSQL, and OpenAI credentials are only ever entered via local terminal / local `.env`; never added to the workspace, Git, logs, or reports
- Git branch: `phase/2-telegram-readonly-collector` (unchanged since Phase 2; no phase has needed a new branch)
- Git remote: `https://github.com/cpcp1117-source/TelegramReading.git`
- Onboarded channels: `@followgerry` (`EXECUTION_SIGNAL`, dynamic Binance-resolved scope); bonnie-blockchain (`ANALYSIS`, authorized but AI path paused)
- Known open items: `P4-MAJOR-001` (edited-message/supersede linking, mandatory before Production Gate 8); FR-013/FR-014 (Strategy Contract/Market Confirmation/Candidate Trade, paused, no forced deadline) — see [docs/phase-5/known-issues.md](docs/phase-5/known-issues.md)
- Next permitted action: Phase 6 planning/kickoff

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

Gate 0 re-review 為 `READY`，使用者已於 2026-09-03 明確批准 Specification v0.2。Phase 1 已於 2026-09-06 取得使用者驗收、合併至 `main`，並建立 `phase-1-accepted` tag。Phase 2–5 均已依序取得 `READY + USER_ACCEPTED`（詳見各 `docs/phase-N/gate-N-checklist.md`）。Phase 5 為風險接受：AI/ANALYSIS 路線（Strategy Contract、Market Confirmation、Candidate Trade）暫停，尚未實作，見 [gate-5-checklist.md](docs/phase-5/gate-5-checklist.md)。目前仍禁止任何真實下單/執行能力，待未來 Phase 開放。

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

`test` service 連線到獨立的 `telegram_trader_test`，不是正式的 `telegram_trader`；整合測試會 `TRUNCATE … CASCADE`，若指向正式資料庫會清掉真實下單紀錄，fixture 也會拒絕名稱不是 `_test` 結尾的資料庫。全新 volume 會由 `docker/postgres-init/` 自動建立測試資料庫；既有 volume 需先手動建立一次：

```powershell
docker compose exec db createdb -U postgres telegram_trader_test
```

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

`dialogs` 只輸出目標頻道摘要與總頻道數，不列出其他頻道名稱。需要回報時，只提供 `target_found`、目標的 `channel_id` 與 `username`；不要提供任何 credential 或 session 檔。

目標確認後，可使用 Docker Compose 的 `telegram` profile 啟動唯讀 Collector。腳本會再次隱藏輸入 Telegram API Hash 與 PostgreSQL password，先執行 migration，再以前景模式啟動，按 `Ctrl+C` 停止：

```powershell
.\scripts\telegram-bootstrap.ps1 -Command collect
```

Collector 僅允許 `channel_id=2439599598`，輸出不含訊息原文或 credentials；圖片存於 Git ignored 的 `media/`，session 存於 `secrets/telegram/`。此命令只是 Phase 2 受控驗證，並不代表 Gate 2 或 24 小時 soak 已通過。
