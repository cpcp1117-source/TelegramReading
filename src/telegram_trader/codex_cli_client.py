from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from telegram_trader.config import Settings
from telegram_trader.openai_client import OpenAiClientError

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class CodexCliClient:
    """`extract_structured` backed by the locally logged-in Codex CLI (`codex exec`).

    Drop-in for `OpenAiClient`: same method, same `OpenAiClientError` on any
    failure, so `thesis_extraction`/`setup_extraction` do not care which one
    they got. Uses the user's ChatGPT subscription login, so no API key is
    configured anywhere. The prompt goes over stdin and the reply is read from
    `--output-last-message`, so nothing here depends on parsing terminal output.
    The sandbox is read-only and the working directory is a throwaway temp
    directory: the model is only ever asked to produce JSON, never to act.
    """

    def __init__(
        self,
        *,
        executable: str,
        timeout_seconds: float,
        runner: Runner = subprocess.run,
    ) -> None:
        self._executable = executable
        self._timeout_seconds = timeout_seconds
        self._runner = runner

    def extract_structured(
        self,
        *,
        model: str,
        system_prompt: str,
        user_content: str,
        json_schema: dict[str, Any],
        schema_name: str,
    ) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="codex-extract-") as workdir:
            schema_path = Path(workdir) / f"{schema_name}.schema.json"
            output_path = Path(workdir) / "last_message.json"
            schema_path.write_text(json.dumps(json_schema), encoding="utf-8")
            command = [
                self._executable,
                "exec",
                "--skip-git-repo-check",
                "--ephemeral",
                "--sandbox",
                "read-only",
                "-m",
                model,
                "--output-schema",
                str(schema_path),
                "-o",
                str(output_path),
                "-",
            ]
            try:
                completed = self._runner(
                    command,
                    input=f"{system_prompt}\n\n{user_content}",
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=self._timeout_seconds,
                    cwd=workdir,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise OpenAiClientError(f"codex CLI could not run: {error}") from error
            if completed.returncode != 0:
                # Last lines of stderr only: enough to see "model not supported" or
                # "login expired" without logging the whole prompt echo.
                tail = (completed.stderr or "").strip().splitlines()[-3:]
                raise OpenAiClientError(
                    f"codex CLI exited {completed.returncode}: {' | '.join(tail)}"
                )
            try:
                result: dict[str, Any] = json.loads(output_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise OpenAiClientError(f"unparseable codex CLI output: {error}") from error
        return result

    def close(self) -> None:
        return None


def create_codex_cli_client(settings: Settings) -> CodexCliClient:
    executable = shutil.which(settings.codex_cli_path)
    if executable is None and os.name == "nt":
        executable = shutil.which(f"{settings.codex_cli_path}.cmd")
    if executable is None:
        raise OpenAiClientError(f"codex CLI not found on PATH: {settings.codex_cli_path!r}")
    return CodexCliClient(executable=executable, timeout_seconds=settings.ai_cli_timeout_seconds)
