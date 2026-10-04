"""Settings the user can change from the Web Console, without editing .env or restarting.

`SETTINGS` is the whole editable surface: every entry names a `Settings` field,
its bounds and how the change takes effect. Changes are stored append-only in
`runtime_setting_changes`; the newest row per key wins and NULL means "back to
the .env default". Services call `load_effective_settings` once per cycle, so the
Web Console never needs a service's credentials or a restart to change a value.

Values travel to and from the browser in display units (percent fields as 20 for
0.20); the database keeps the `Settings` field's own unit.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from telegram_trader.audit import append_audit_event
from telegram_trader.config import Settings
from telegram_trader.models import RuntimeSettingChange

LOGGER = logging.getLogger(__name__)

SETTINGS_CHANGED_EVENT_TYPE = "settings_changed"

LIVE_RISK = "下一筆風控判斷就生效（約 5 秒內）；Telegram 預覽約 1 分鐘內更新"


@dataclass(frozen=True, slots=True)
class SettingSpec:
    key: str
    group: str
    label: str
    help: str
    kind: str  # "int" | "decimal" | "float"
    minimum: Decimal
    maximum: Decimal
    effect: str
    unit: str = ""
    percent: bool = False


SETTINGS: tuple[SettingSpec, ...] = (
    SettingSpec(
        "risk_equity_baseline_usdt",
        "risk",
        "資金基準",
        "計算每筆倉位大小和每日虧損比例用的資金金額。",
        "decimal",
        Decimal("1"),
        Decimal("1000000000"),
        LIVE_RISK,
        unit="USDT",
    ),
    SettingSpec(
        "risk_position_size_pct",
        "risk",
        "每筆倉位大小",
        "每筆下單的保證金佔資金基準的比例；核准前仍可在 Telegram 草稿個別調整。",
        "decimal",
        Decimal("1"),
        Decimal("100"),
        LIVE_RISK,
        unit="%",
        percent=True,
    ),
    SettingSpec(
        "risk_leverage",
        "risk",
        "槓桿",
        "下單時設定的槓桿倍數（逐倉）。",
        "int",
        Decimal("1"),
        Decimal("50"),
        LIVE_RISK,
        unit="倍",
    ),
    SettingSpec(
        "risk_default_stop_roe_pct",
        "risk",
        "預設停損（保證金報酬率）",
        "訊號沒有停損時，以保證金虧損這個比例的價格當停損。",
        "decimal",
        Decimal("1"),
        Decimal("100"),
        LIVE_RISK,
        unit="%",
        percent=True,
    ),
    SettingSpec(
        "risk_daily_loss_kill_switch_pct",
        "risk",
        "每日虧損熔斷",
        "今日（台灣 08:00 起）已實現虧損達資金基準的這個比例時，Telegram 詢問是否停止今日開單。",
        "decimal",
        Decimal("0.5"),
        Decimal("50"),
        "下一筆平倉時就用新門檻判斷",
        unit="%",
        percent=True,
    ),
    SettingSpec(
        "risk_max_concurrent_positions",
        "risk",
        "最多同時持倉數",
        "超過時只在核准訊息裡提醒，不會擋單。",
        "int",
        Decimal("1"),
        Decimal("50"),
        LIVE_RISK,
        unit="筆",
    ),
    SettingSpec(
        "risk_max_receive_lag_seconds",
        "risk",
        "訊號最大延遲",
        "訊號從作者發布到系統收到超過這個秒數，風控就拒絕。",
        "int",
        Decimal("1"),
        Decimal("86400"),
        LIVE_RISK,
        unit="秒",
    ),
    SettingSpec(
        "risk_max_price_deviation_bps",
        "risk",
        "市價單最大價格偏離",
        "市價單：現價和訊號發布時的價格差超過這個值就拒絕。1 bps = 0.01%。",
        "int",
        Decimal("1"),
        Decimal("5000"),
        LIVE_RISK,
        unit="bps",
    ),
    SettingSpec(
        "risk_decision_expiry_seconds",
        "risk",
        "風控通過後的下單期限",
        "風控通過後超過這個秒數還沒送出下單，這筆就作廢。",
        "float",
        Decimal("10"),
        Decimal("86400"),
        LIVE_RISK,
        unit="秒",
    ),
    SettingSpec(
        "control_bot_poll_interval_seconds",
        "runtime",
        "新訊號檢查間隔",
        "Telegram 機器人多久檢查一次有沒有新訊號要請你核准。",
        "float",
        Decimal("5"),
        Decimal("3600"),
        "control-bot 下一次檢查後就改用新間隔",
        unit="秒",
    ),
    SettingSpec(
        "policy_poll_interval_seconds",
        "runtime",
        "頻道政策重新檢查間隔",
        "collector 多久重新讀一次頻道授權設定（撤銷授權時會停止收集）。",
        "float",
        Decimal("30"),
        Decimal("86400"),
        "需重啟 collector 才生效",
        unit="秒",
    ),
    SettingSpec(
        "binance_snapshot_max_age_seconds",
        "runtime",
        "幣種清單更新間隔",
        "Binance 可交易幣種清單多久重新抓一次（訊號解析、分析監控用）。",
        "float",
        Decimal("600"),
        Decimal("604800"),
        "需重啟分析監控服務才生效",
        unit="秒",
    ),
)

SPECS: dict[str, SettingSpec] = {spec.key: spec for spec in SETTINGS}


class SettingValueError(ValueError):
    """A value the user typed is not acceptable; the message is shown as-is."""


def _plain(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def parse_display_value(spec: SettingSpec, raw: object) -> Decimal:
    """Browser input (display units) -> validated number in display units."""
    try:
        number = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError) as error:
        raise SettingValueError(f"{spec.label}：請輸入數字") from error
    if not number.is_finite():
        raise SettingValueError(f"{spec.label}：請輸入數字")
    if spec.kind == "int" and number != number.to_integral_value():
        raise SettingValueError(f"{spec.label}：請輸入整數")
    if not spec.minimum <= number <= spec.maximum:
        raise SettingValueError(
            f"{spec.label}：必須介於 {_plain(spec.minimum)} 到 {_plain(spec.maximum)}{spec.unit}"
        )
    return number


def to_stored(spec: SettingSpec, display: Decimal) -> str:
    return _plain(display / 100 if spec.percent else display)


def to_display(spec: SettingSpec, stored: object) -> str | None:
    if stored is None:
        return None
    number = Decimal(str(stored))
    return _plain(number * 100 if spec.percent else number)


def _field_value(spec: SettingSpec, stored: str) -> Any:
    number = Decimal(stored)
    if spec.kind == "int":
        return int(number)
    if spec.kind == "float":
        return float(number)
    return number


def latest_changes(session: Session) -> dict[str, RuntimeSettingChange]:
    rows = session.scalars(
        select(RuntimeSettingChange)
        .distinct(RuntimeSettingChange.key)
        .order_by(
            RuntimeSettingChange.key,
            RuntimeSettingChange.changed_at.desc(),
            RuntimeSettingChange.change_id.desc(),
        )
    )
    return {row.key: row for row in rows}


def apply_overrides(session: Session, base: Settings) -> Settings:
    """`base` with every Web Console value in force laid over it.

    A stored value that no longer passes its spec (bounds changed in code) is
    skipped with a warning, so a bad row can never stop a service.
    """
    updates: dict[str, Any] = {}
    for key, change in latest_changes(session).items():
        spec = SPECS.get(key)
        if spec is None or change.value is None:
            continue
        try:
            display = parse_display_value(spec, to_display(spec, change.value))
        except SettingValueError:
            LOGGER.warning(
                "ignoring out-of-range web setting",
                extra={"context": {"key": key, "value": change.value}},
            )
            continue
        updates[key] = _field_value(spec, to_stored(spec, display))
    return base.model_copy(update=updates) if updates else base


def load_effective_settings(session_factory: sessionmaker[Session], base: Settings) -> Settings:
    """`apply_overrides` in its own session; falls back to `base` if the database is down."""
    try:
        with session_factory() as session:
            return apply_overrides(session, base)
    except Exception:
        LOGGER.exception("could not load web settings; using .env values for this cycle")
        return base


def effective_loader(
    session_factory: sessionmaker[Session], base: Settings
) -> Callable[[], Settings]:
    return lambda: load_effective_settings(session_factory, base)


def describe(session: Session, base: Settings, group: str) -> list[dict[str, Any]]:
    """Everything a settings page shows per item, in display units."""
    latest = latest_changes(session)
    items = []
    for spec in SETTINGS:
        if spec.group != group:
            continue
        change = latest.get(spec.key)
        env_value = to_display(spec, getattr(base, spec.key))
        override = to_display(spec, change.value) if change is not None else None
        items.append(
            {
                "key": spec.key,
                "label": spec.label,
                "help": spec.help,
                "unit": spec.unit,
                "kind": spec.kind,
                "minimum": _plain(spec.minimum),
                "maximum": _plain(spec.maximum),
                "effect": spec.effect,
                "env_value": env_value,
                "override": override,
                "effective": override if override is not None else env_value,
                "changed_at": change.changed_at.isoformat() if change is not None else None,
                "changed_by": change.changed_by if change is not None else None,
            }
        )
    return items


def record_changes(
    session: Session,
    base: Settings,
    changes: dict[str, object],
    *,
    actor: str,
    note: str | None,
    now: datetime,
) -> list[dict[str, Any]]:
    """Validate every change first, then append one row per value that actually changed.

    `changes` maps key -> display value, or None for "back to the .env default".
    All-or-nothing: one invalid value rejects the whole save. Returns the diffs.
    """
    latest = latest_changes(session)
    pending: list[tuple[SettingSpec, str | None, str | None]] = []
    for key, raw in changes.items():
        spec = SPECS.get(key)
        if spec is None:
            raise SettingValueError(f"不能修改的設定：{key}")
        stored = None if raw is None else to_stored(spec, parse_display_value(spec, raw))
        current = latest.get(key)
        current_stored = current.value if current is not None else None
        if stored == current_stored:
            continue
        if current_stored is None and stored == _plain(Decimal(str(getattr(base, key)))):
            continue  # typing the .env value again is not an override
        pending.append((spec, current_stored, stored))

    diffs = []
    for spec, previous, stored in pending:
        change_id = hashlib.sha256(f"{spec.key}:{now.isoformat()}:{stored}".encode()).hexdigest()
        session.add(
            RuntimeSettingChange(
                change_id=change_id,
                key=spec.key,
                value=stored,
                previous_value=previous,
                changed_by=actor,
                note=note,
                changed_at=now,
            )
        )
        env_value = to_display(spec, getattr(base, spec.key))
        diffs.append(
            {
                "key": spec.key,
                "label": spec.label,
                "unit": spec.unit,
                "from": to_display(spec, previous) if previous is not None else env_value,
                "to": to_display(spec, stored) if stored is not None else env_value,
                "reset_to_env": stored is None,
            }
        )
    if diffs:
        append_audit_event(
            session,
            event_id=hashlib.sha256(f"settings:{now.isoformat()}:{actor}".encode()).hexdigest(),
            event_type=SETTINGS_CHANGED_EVENT_TYPE,
            aggregate_type="runtime_settings",
            aggregate_id=",".join(sorted(spec.key for spec, _, _ in pending))[:200],
            payload={"actor": actor, "note": note, "changes": diffs},
        )
    return diffs


def history(session: Session, group: str, limit: int = 50) -> list[dict[str, Any]]:
    keys = [spec.key for spec in SETTINGS if spec.group == group]
    rows = session.scalars(
        select(RuntimeSettingChange)
        .where(RuntimeSettingChange.key.in_(keys))
        .order_by(RuntimeSettingChange.changed_at.desc(), RuntimeSettingChange.change_id)
        .limit(limit)
    )
    return [
        {
            "key": row.key,
            "label": SPECS[row.key].label,
            "unit": SPECS[row.key].unit,
            "from": to_display(SPECS[row.key], row.previous_value),
            "to": to_display(SPECS[row.key], row.value),
            "changed_by": row.changed_by,
            "note": row.note,
            "changed_at": row.changed_at.isoformat(),
        }
        for row in rows
    ]
