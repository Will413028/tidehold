from pathlib import Path

import pytest
from psycopg.conninfo import conninfo_to_dict

from tidehold.platform.settings import Settings, database_url_from_env, from_env


def test_cookies_are_secure_unless_the_origin_is_plain_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("PUBLIC_ORIGIN", "https://tidehold.example")
    assert from_env().cookie_secure is True

    monkeypatch.setenv("PUBLIC_ORIGIN", "http://localhost:3000")
    assert from_env().cookie_secure is False


@pytest.mark.parametrize(
    "origin", ["tidehold.example", "https://tidehold.example/", "ftp://tidehold.example"]
)
def test_an_origin_with_a_path_or_another_scheme_is_refused(origin: str) -> None:
    with pytest.raises(ValueError, match="PUBLIC_ORIGIN"):
        Settings(database_url="postgresql://unused", public_origin=origin)


def test_the_database_password_can_come_from_a_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    written = "s3cret with spaces"
    secret = tmp_path / "db_password"
    secret.write_text(f"{written}\n")
    monkeypatch.setenv("DATABASE_URL", "postgresql://tidehold@db:5432/tidehold")
    monkeypatch.setenv("DATABASE_PASSWORD_FILE", str(secret))

    parts = conninfo_to_dict(database_url_from_env())

    assert parts["password"] == written
    settings = Settings(database_url=database_url_from_env(), public_origin="https://t.example")
    assert written not in repr(settings)
    assert (parts["user"], parts["host"], parts["dbname"]) == ("tidehold", "db", "tidehold")


def test_without_a_password_file_the_url_is_used_as_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:dev@127.0.0.1:5433/tidehold")
    monkeypatch.delenv("DATABASE_PASSWORD_FILE", raising=False)

    assert database_url_from_env() == "postgresql://postgres:dev@127.0.0.1:5433/tidehold"
