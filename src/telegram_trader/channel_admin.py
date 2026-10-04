"""Create and edit `channel_policies` rows from the Web Console.

The table stays the single runtime source of truth: the collector, normalizer,
signal parser and AI extraction all read it. Every create/update writes an audit
event with the field-level diff. Nothing is hard-deleted: "remove" sets
`gate_decision=REJECTED`, which stops collection while the row keeps driving
retention cleanup of data already collected (a missing row would mean that data
is never cleaned up -- see `retention_cleanup.load_retention_targets`).
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from telegram_trader.audit import append_audit_event
from telegram_trader.models import ChannelPolicy, TelegramMessageVersion

CHANNEL_TYPES = {"EXECUTION_SIGNAL": "下單訊號頻道", "ANALYSIS": "分析頻道"}
AUTHORIZATIONS = {
    "GRANTED": "已授權",
    "PENDING": "申請中",
    "UNKNOWN": "未確認",
    "REVOKED": "已撤銷",
}
GATE_DECISIONS = {
    "MONITOR_ONLY": "監看中",
    "ENABLED": "啟用",
    "PAUSED": "暫停",
    "REJECTED": "已移除",
}
SYMBOL_SCOPES = {
    "BINANCE_USDM_ACTIVE_PERPETUAL": "所有 Binance U 本位永續合約",
    "STATIC_ALLOWLIST": "只限指定幣種",
}
CONTENT_TYPES = ("TEXT", "CAPTION", "IMAGE")
AUTH_FIELDS = (
    "access_authorization",
    "automation_authorization",
    "ai_authorization",
    "media_authorization",
)
EDITABLE_FIELDS = (
    "label",
    "username",
    "channel_type",
    *AUTH_FIELDS,
    "gate_decision",
    "symbol_scope_mode",
    "allowed_symbols",
    "prohibited_symbols",
    "message_languages",
    "supported_content_types",
    "raw_retention_days",
    "onboarding_doc_path",
    "acceptance_owner",
    "acceptance_date",
)

_USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
_SYMBOL = re.compile(r"^[A-Z0-9]{2,20}$")
_LANGUAGE = re.compile(r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")


class ChannelValueError(ValueError):
    """Invalid form input; the message is shown to the user as-is."""


def _choice(data: dict[str, Any], field: str, choices: dict[str, str], label: str) -> str:
    value = str(data.get(field) or "").strip()
    if value not in choices:
        raise ChannelValueError(f"{label}：請選擇 {'、'.join(choices.values())} 其中一個")
    return value


def _string_list(raw: object, pattern: re.Pattern[str], label: str, *, upper: bool) -> list[str]:
    items = raw if isinstance(raw, list) else re.split(r"[\s,，、]+", str(raw or ""))
    cleaned: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        text = text.upper() if upper else text
        if not pattern.match(text):
            raise ChannelValueError(f"{label}：「{text}」格式不正確")
        if text not in cleaned:
            cleaned.append(text)
    return cleaned


def parse_channel_identity(channel_raw: object, topic_raw: object) -> tuple[int, int]:
    """Accepts the `-100…` form Telegram clients show for channel IDs."""
    text = str(channel_raw or "").strip()
    if text.startswith("-100"):
        text = text[4:]
    try:
        channel_id = int(text)
    except ValueError as error:
        raise ChannelValueError("頻道 ID：請輸入數字") from error
    if channel_id <= 0:
        raise ChannelValueError("頻道 ID：必須是正數")
    topic_text = str(topic_raw if topic_raw is not None else "").strip() or "0"
    try:
        topic_id = int(topic_text)
    except ValueError as error:
        raise ChannelValueError("討論串 ID：請輸入數字（整個頻道填 0）") from error
    if topic_id < 0:
        raise ChannelValueError("討論串 ID：不能是負數（整個頻道填 0）")
    return channel_id, topic_id


def validate_fields(data: dict[str, Any]) -> dict[str, Any]:
    """Form input -> clean column values for every editable field."""
    label = str(data.get("label") or "").strip()
    if not label or len(label) > 200:
        raise ChannelValueError("名稱：必填，最多 200 字")
    username = str(data.get("username") or "").strip().lstrip("@") or None
    if username is not None and not _USERNAME.match(username):
        raise ChannelValueError("公開帳號：5–32 個英數字或底線，不含 @；私人頻道請留空")

    values: dict[str, Any] = {
        "label": label,
        "username": username,
        "channel_type": _choice(data, "channel_type", CHANNEL_TYPES, "頻道類型"),
        "gate_decision": _choice(data, "gate_decision", GATE_DECISIONS, "狀態"),
        "symbol_scope_mode": _choice(data, "symbol_scope_mode", SYMBOL_SCOPES, "幣種範圍"),
    }
    for field, field_label in zip(
        AUTH_FIELDS, ("讀取內容授權", "自動收集授權", "AI 處理授權", "圖片儲存授權"), strict=True
    ):
        values[field] = _choice(data, field, AUTHORIZATIONS, field_label)

    values["allowed_symbols"] = _string_list(
        data.get("allowed_symbols"), _SYMBOL, "允許幣種", upper=True
    )
    values["prohibited_symbols"] = _string_list(
        data.get("prohibited_symbols"), _SYMBOL, "排除幣種", upper=True
    )
    if values["symbol_scope_mode"] == "STATIC_ALLOWLIST" and not values["allowed_symbols"]:
        raise ChannelValueError("幣種範圍選「只限指定幣種」時，允許幣種至少要填一個")
    values["message_languages"] = _string_list(
        data.get("message_languages"), _LANGUAGE, "訊息語言", upper=False
    )
    content_types = data.get("supported_content_types") or []
    if not isinstance(content_types, list) or any(t not in CONTENT_TYPES for t in content_types):
        raise ChannelValueError("內容類型：只能選 文字、圖片說明、圖片")
    values["supported_content_types"] = [t for t in CONTENT_TYPES if t in content_types]

    try:
        retention = int(str(data.get("raw_retention_days") or "").strip())
    except ValueError as error:
        raise ChannelValueError("原始訊息保留天數：請輸入整數") from error
    if not 1 <= retention <= 3650:
        raise ChannelValueError("原始訊息保留天數：必須介於 1 到 3650 天")
    values["raw_retention_days"] = retention

    doc_path = str(data.get("onboarding_doc_path") or "").strip() or None
    if doc_path is not None and len(doc_path) > 500:
        raise ChannelValueError("說明文件路徑：最多 500 字")
    values["onboarding_doc_path"] = doc_path
    owner = str(data.get("acceptance_owner") or "").strip() or None
    if owner is not None and len(owner) > 200:
        raise ChannelValueError("授權確認人：最多 200 字")
    values["acceptance_owner"] = owner
    raw_date = str(data.get("acceptance_date") or "").strip()
    try:
        values["acceptance_date"] = date.fromisoformat(raw_date) if raw_date else None
    except ValueError as error:
        raise ChannelValueError("授權確認日期：格式應為 YYYY-MM-DD") from error
    return values


def _json_value(value: Any) -> Any:
    return value.isoformat() if isinstance(value, date) else value


def channel_row(policy: ChannelPolicy) -> dict[str, Any]:
    row = {field: _json_value(getattr(policy, field)) for field in EDITABLE_FIELDS}
    row.update(
        channel_id=policy.channel_id,
        topic_id=policy.topic_id,
        policy_detail=policy.policy_detail,
        created_at=policy.created_at.isoformat() if policy.created_at else None,
        updated_at=policy.updated_at.isoformat() if policy.updated_at else None,
    )
    return row


def list_channel_policies(session: Session) -> list[dict[str, Any]]:
    """Every policy row plus when a message from it was last stored (collection health)."""
    last_seen = {
        (channel_id, topic_id or 0): (received_at, count)
        for channel_id, topic_id, received_at, count in session.execute(
            select(
                TelegramMessageVersion.channel_id,
                TelegramMessageVersion.topic_id,
                func.max(TelegramMessageVersion.received_at),
                func.count(),
            ).group_by(TelegramMessageVersion.channel_id, TelegramMessageVersion.topic_id)
        )
    }
    items = []
    for policy in session.scalars(
        select(ChannelPolicy).order_by(ChannelPolicy.created_at, ChannelPolicy.channel_id)
    ):
        row = channel_row(policy)
        received_at, count = last_seen.get((policy.channel_id, policy.topic_id), (None, 0))
        row["last_message_at"] = received_at.isoformat() if received_at else None
        row["stored_messages"] = count
        items.append(row)
    return items


def _audit(
    session: Session,
    event_type: str,
    policy: ChannelPolicy,
    payload: dict[str, Any],
    *,
    actor: str,
) -> None:
    key = f"{policy.channel_id}:{policy.topic_id}"
    append_audit_event(
        session,
        event_id=f"chan-{uuid.uuid4().hex}",
        event_type=event_type,
        aggregate_type="channel_policy",
        aggregate_id=key,
        payload={"actor": actor, "label": policy.label, **payload},
    )


def create_channel(
    session: Session, data: dict[str, Any], *, actor: str, now: datetime
) -> ChannelPolicy:
    channel_id, topic_id = parse_channel_identity(data.get("channel_id"), data.get("topic_id"))
    if session.get(ChannelPolicy, (channel_id, topic_id)) is not None:
        raise ChannelValueError("這個頻道（含討論串）已經存在，請直接修改")
    values = validate_fields(data)
    policy = ChannelPolicy(
        channel_id=channel_id,
        topic_id=topic_id,
        policy_detail={},
        created_at=now,
        updated_at=now,
        **values,
    )
    session.add(policy)
    session.flush()
    _audit(
        session,
        "channel_policy_created",
        policy,
        {"values": {k: _json_value(v) for k, v in values.items()}},
        actor=actor,
    )
    return policy


def update_channel(
    session: Session,
    channel_id: int,
    topic_id: int,
    data: dict[str, Any],
    *,
    actor: str,
    now: datetime,
) -> list[dict[str, Any]]:
    """Apply the full form; returns the changed fields (empty when nothing changed)."""
    policy = session.get(ChannelPolicy, (channel_id, topic_id))
    if policy is None:
        raise LookupError("channel policy not found")
    values = validate_fields(data)
    diffs = []
    for field, new in values.items():
        old = getattr(policy, field)
        if old != new:
            diffs.append({"field": field, "from": _json_value(old), "to": _json_value(new)})
            setattr(policy, field, new)
    if diffs:
        policy.updated_at = now
        _audit(session, "channel_policy_updated", policy, {"changes": diffs}, actor=actor)
    return diffs
