import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    database_url: str
    # The one origin browsers use (the web app, which proxies /api); state-changing requests
    # from any other origin are refused.
    public_origin: str
    # New PayPal payments (checkout or test-card attempts) a workspace may open per UTC day;
    # protects the sandbox merchant shared with the fallback demo (threat model surface 2).
    paypal_daily_open_cap: int = 30
    # Sandbox credentials; without them the PayPal routes answer 503.
    paypal: "PayPalCredentials | None" = None
    # The due_actions worker runs inside the API process (one host, runtime ADR).
    run_worker: bool = False

    def __post_init__(self) -> None:
        parts = urlsplit(self.public_origin)
        if parts.scheme not in ("https", "http") or not parts.netloc or parts.path:
            raise ValueError("PUBLIC_ORIGIN must be scheme://host[:port]")

    @property
    def cookie_secure(self) -> bool:
        """Browsers drop Secure cookies over plain http, so only local http development
        gets a cookie without it."""
        return self.public_origin.startswith("https://")


@dataclass(frozen=True)
class PayPalCredentials:
    client_id: str
    client_secret: str = field(repr=False)


def paypal_from_env() -> PayPalCredentials:
    """Sandbox credentials; refused unless PAYPAL_ENV says sandbox, so a live key pair
    cannot be loaded by mistake (the client is also pinned to the sandbox URL)."""
    if os.environ.get("PAYPAL_ENV") != "sandbox":
        raise ValueError("PAYPAL_ENV must be sandbox")
    return PayPalCredentials(os.environ["PAYPAL_CLIENT_ID"], os.environ["PAYPAL_CLIENT_SECRET"])


def database_url_from_env() -> str:
    """What the migration command needs, without the web settings."""
    return os.environ["DATABASE_URL"]


def from_env() -> Settings:
    return Settings(
        database_url=database_url_from_env(),
        public_origin=os.environ["PUBLIC_ORIGIN"],
        paypal_daily_open_cap=int(os.environ.get("PAYPAL_DAILY_OPEN_CAP", "30")),
        paypal=paypal_from_env() if "PAYPAL_CLIENT_ID" in os.environ else None,
        run_worker=os.environ.get("RUN_WORKER", "true") != "false",
    )
