import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    # False only for local http development; browsers drop Secure cookies over plain http.
    cookie_secure: bool


def from_env() -> Settings:
    return Settings(
        database_url=os.environ["DATABASE_URL"],
        cookie_secure=os.environ.get("COOKIE_SECURE", "true") != "false",
    )
