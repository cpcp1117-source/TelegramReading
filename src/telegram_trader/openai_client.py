from __future__ import annotations

import json
from typing import Any

import httpx

from telegram_trader.config import Settings

_CHAT_COMPLETIONS_PATH = "/v1/chat/completions"


class OpenAiClientError(RuntimeError):
    """Any transport failure, non-2xx response, or unparseable content."""


class OpenAiClient:
    """Thin synchronous wrapper over OpenAI's Chat Completions API, using

    Structured Outputs (`response_format=json_schema`, strict mode) for
    schema-valid extraction. Exposes exactly the one method production code
    calls, matching this project's `FakeXClient` test convention (a
    `FakeOpenAiClient` implementing only `extract_structured` can stand in
    for this class in tests -- no mocking library needed). This client has
    no Binance credential, no risk-rule write access, and no execution tool
    reachable from it at all (BR-013): it only knows how to POST to one
    endpoint and parse the response.
    """

    def __init__(self, http_client: httpx.Client) -> None:
        self._http_client = http_client

    def extract_structured(
        self,
        *,
        model: str,
        system_prompt: str,
        user_content: str,
        json_schema: dict[str, Any],
        schema_name: str,
    ) -> dict[str, Any]:
        try:
            response = self._http_client.post(
                _CHAT_COMPLETIONS_PATH,
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": schema_name,
                            "strict": True,
                            "schema": json_schema,
                        },
                    },
                    "temperature": 0,
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise OpenAiClientError(f"OpenAI request failed: {error}") from error

        payload = response.json()
        try:
            content = payload["choices"][0]["message"]["content"]
            result: dict[str, Any] = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
            raise OpenAiClientError(f"unparseable OpenAI response: {error}") from error
        return result

    def close(self) -> None:
        self._http_client.close()


def create_openai_client(settings: Settings) -> OpenAiClient:
    if settings.openai_api_key is None:
        raise OpenAiClientError("OPENAI_API_KEY is required to run thesis extraction")
    http_client = httpx.Client(
        base_url=settings.openai_api_base_url,
        headers={"Authorization": f"Bearer {settings.openai_api_key.get_secret_value()}"},
        timeout=settings.openai_request_timeout_seconds,
    )
    return OpenAiClient(http_client)
