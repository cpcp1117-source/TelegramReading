from __future__ import annotations

from pathlib import Path

from telegram_trader.secret_scan import scan


def test_secret_scanner_accepts_empty_example(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text("APP_DATABASE_URL=\n", encoding="utf-8")
    count, findings = scan(tmp_path)
    assert count == 1
    assert findings == []


def test_secret_scanner_detects_private_key_marker(tmp_path: Path) -> None:
    private_key_marker = "-----BEGIN " + "PRIVATE KEY-----"
    (tmp_path / "leak.txt").write_text(
        f"{private_key_marker}\nnot-real-test-material\n", encoding="utf-8"
    )
    _, findings = scan(tmp_path)
    assert len(findings) == 1
    assert "private key material" in findings[0]


def test_secret_scanner_skips_non_utf8_file(tmp_path: Path) -> None:
    (tmp_path / "binary.txt").write_bytes(b"\xff\xfe\xfa")
    count, findings = scan(tmp_path)
    assert count == 1
    assert findings == []


def test_secret_scanner_ignores_excluded_directory(tmp_path: Path) -> None:
    excluded = tmp_path / ".git"
    excluded.mkdir()
    marker = "-----BEGIN " + "PRIVATE KEY-----"
    (excluded / "ignored.txt").write_text(marker, encoding="utf-8")
    count, findings = scan(tmp_path)
    assert count == 0
    assert findings == []


def test_secret_scanner_detects_non_placeholder_assignment(tmp_path: Path) -> None:
    field_name = "api_" + "key"
    (tmp_path / "leak.env").write_text(f"{field_name}=not-a-placeholder\n", encoding="utf-8")
    _, findings = scan(tmp_path)
    assert len(findings) == 1
    assert "credential assignment" in findings[0]


def test_secret_scanner_ignores_gitignored_local_env_file(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("POSTGRES_PASSWORD=local-only-value\n", encoding="utf-8")

    count, findings = scan(tmp_path)

    assert count == 0
    assert findings == []


def test_secret_scanner_ignores_python_secret_type_declaration(tmp_path: Path) -> None:
    (tmp_path / "config.py").write_text(
        "database_password: SecretStr | None = None\n",
        encoding="utf-8",
    )

    count, findings = scan(tmp_path)

    assert count == 1
    assert findings == []


def test_secret_scanner_detects_python_secret_literal(tmp_path: Path) -> None:
    field_name = "database_" + "password"
    (tmp_path / "config.py").write_text(
        f'{field_name} = "not-a-placeholder"\n',
        encoding="utf-8",
    )

    _, findings = scan(tmp_path)

    assert len(findings) == 1
    assert "credential assignment" in findings[0]
