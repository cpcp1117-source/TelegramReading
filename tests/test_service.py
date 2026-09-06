from __future__ import annotations

import telegram_trader.service as service_module


def test_service_runs_migration_before_server(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[str] = []

    monkeypatch.setattr(
        service_module, "upgrade", lambda _config, revision: calls.append(f"migrate:{revision}")
    )
    monkeypatch.setattr(
        service_module,
        "run",
        lambda *_args, **_kwargs: calls.append("serve"),
    )

    service_module.main()

    assert calls == ["migrate:head", "serve"]
