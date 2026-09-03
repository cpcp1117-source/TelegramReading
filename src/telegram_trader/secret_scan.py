from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

EXCLUDED_PARTS = {
    ".git",
    ".git-sandbox-init-backup",
    ".mypy_cache",
    ".pytest_cache",
    ".pytest_tmp",
    ".ruff_cache",
    ".uv-cache",
    ".venv",
    "__pycache__",
    "htmlcov",
}
TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".env",
    ".example",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".sh",
    ".sql",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}

PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)(api[_-]?(?:key|hash|secret)|bot[_-]?token|password|session[_-]?string)"
    r"\s*[:=]\s*['\"]?([^\s'\"#]+)"
)
PLACEHOLDERS = {
    "",
    "[redacted]",
    "change_me",
    "empty",
    "none",
    "null",
    "placeholder",
    "redacted",
}


def iter_text_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        relative_parts = path.relative_to(root).parts
        if not path.is_file() or any(part in EXCLUDED_PARTS for part in relative_parts):
            continue
        if path.name == ".env.example" or path.suffix.lower() in TEXT_SUFFIXES:
            yield path


def scan_file(path: Path) -> list[str]:
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    findings: list[str] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        if PRIVATE_KEY.search(line):
            findings.append(f"{path}:{line_number}: private key material")
        for match in CREDENTIAL_ASSIGNMENT.finditer(line):
            value = match.group(2).strip().lower()
            if value not in PLACEHOLDERS and not value.startswith(("${", "<", "{{")):
                findings.append(f"{path}:{line_number}: non-placeholder credential assignment")
    return findings


def scan(root: Path) -> tuple[int, list[str]]:
    files = list(iter_text_files(root))
    findings = [finding for path in files for finding in scan_file(path)]
    return len(files), findings
