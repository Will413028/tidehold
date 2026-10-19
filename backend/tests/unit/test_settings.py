import pytest

from tidehold.platform.settings import from_env


def test_cookies_are_secure_unless_explicitly_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    assert from_env().cookie_secure is True

    monkeypatch.setenv("COOKIE_SECURE", "false")
    assert from_env().cookie_secure is False
