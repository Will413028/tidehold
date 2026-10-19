import os
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    database_url: str
    # The one origin browsers use (the web app, which proxies /api); state-changing requests
    # from any other origin are refused.
    public_origin: str

    def __post_init__(self) -> None:
        parts = urlsplit(self.public_origin)
        if parts.scheme not in ("https", "http") or not parts.netloc or parts.path:
            raise ValueError("PUBLIC_ORIGIN must be scheme://host[:port]")

    @property
    def cookie_secure(self) -> bool:
        """Browsers drop Secure cookies over plain http, so only local http development
        gets a cookie without it."""
        return self.public_origin.startswith("https://")


def database_url_from_env() -> str:
    """What the migration command needs, without the web settings."""
    return os.environ["DATABASE_URL"]


def from_env() -> Settings:
    return Settings(
        database_url=database_url_from_env(),
        public_origin=os.environ["PUBLIC_ORIGIN"],
    )
