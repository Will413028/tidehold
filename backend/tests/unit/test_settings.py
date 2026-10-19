import pytest

from tidehold.platform.settings import Settings, from_env


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
