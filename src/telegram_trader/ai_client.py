from __future__ import annotations

from typing import Any

from telegram_trader.codex_cli_client import create_codex_cli_client
from telegram_trader.config import Settings
from telegram_trader.openai_client import create_openai_client


def create_ai_client(settings: Settings) -> Any:
    """The structured-extraction client selected by `AI_PROVIDER` (`openai` or `codex`).

    Both expose `extract_structured(...)` and raise `OpenAiClientError` on failure, so
    callers never branch on the provider.
    """
    if settings.ai_provider == "codex":
        return create_codex_cli_client(settings)
    return create_openai_client(settings)
