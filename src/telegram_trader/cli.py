from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from telegram_trader.config import get_settings
from telegram_trader.db import create_db_engine, create_session_factory
from telegram_trader.mock_telegram import MockMessageProcessor, MockTelegramMessage, load_fixture
from telegram_trader.models import AuditEvent, MockMessageReceipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 1 offline simulator")
    subparsers = parser.add_subparsers(dest="command", required=True)

    simulate = subparsers.add_parser("simulate", help="Replay a JSON fixture")
    simulate.add_argument("--fixture", type=Path, required=True)
    simulate.add_argument("--consumer", default="offline-mock-collector")

    emit = subparsers.add_parser("emit", help="Emit one synthetic message")
    emit.add_argument("--consumer", required=True)
    emit.add_argument("--channel-id", default="mock-channel")
    emit.add_argument("--message-id", type=int, required=True)
    emit.add_argument("--edit-version", type=int, default=0)
    emit.add_argument("--sequence", type=int, required=True)
    emit.add_argument("--text", required=True)

    inspect = subparsers.add_parser("inspect", help="Inspect checkpoint and event counts")
    inspect.add_argument("--consumer", required=True)
    return parser


def _components(consumer: str) -> tuple[MockMessageProcessor, Any, Any]:
    engine = create_db_engine(get_settings())
    factory = create_session_factory(engine)
    return MockMessageProcessor(factory, consumer), factory, engine


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def main() -> int:
    args = _parser().parse_args()
    processor, factory, engine = _components(args.consumer)
    try:
        if args.command == "simulate":
            messages = load_fixture(args.fixture.read_text(encoding="utf-8"))
            results = [asdict(processor.process(message)) for message in messages]
            _print({"results": results, "checkpoint": processor.checkpoint()})
            return 0

        if args.command == "emit":
            message = MockTelegramMessage(
                channel_id=args.channel_id,
                message_id=args.message_id,
                edit_version=args.edit_version,
                sequence=args.sequence,
                text=args.text,
            )
            _print(asdict(processor.process(message)))
            return 0

        with factory() as session:
            receipt_count = session.scalar(
                select(func.count())
                .select_from(MockMessageReceipt)
                .where(MockMessageReceipt.consumer_name == args.consumer)
            )
            audit_count = session.scalar(select(func.count()).select_from(AuditEvent))
        _print(
            {
                "consumer": args.consumer,
                "checkpoint": processor.checkpoint(),
                "receipt_count": receipt_count,
                "audit_count": audit_count,
            }
        )
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
