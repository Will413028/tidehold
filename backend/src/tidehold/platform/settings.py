import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from psycopg.conninfo import make_conninfo

# D22: the developer's choice from the run at prompt 00bdb5c3b65fa352 (model-benchmark.md).
DEFAULT_COMPILE_MODEL = "anthropic:claude-sonnet-5-5"


@dataclass(frozen=True)
class Settings:
    # may carry the password (DATABASE_PASSWORD_FILE), so it is never shown
    database_url: str = field(repr=False)
    # The one origin browsers use (the web app, which proxies /api); state-changing requests
    # from any other origin are refused.
    public_origin: str
    # New PayPal payments (checkout or test-card attempts) a workspace may open per UTC day;
    # protects the sandbox merchant shared with the fallback demo (threat model surface 2).
    paypal_daily_open_cap: int = 30
    # Workspaces one client address may create per UTC day (D15, plan step 10a); the public
    # demo has no accounts, so this bounds how fast anyone fills the database.
    workspace_daily_cap_per_address: int = 20
    # Sandbox credentials; without them the PayPal routes answer 503.
    paypal: "PayPalCredentials | None" = None
    # The due_actions worker runs inside the API process (one host, runtime ADR).
    run_worker: bool = False
    # The policy compiler's model as `vendor:exact-model-id` (D6 provisional ranking) and
    # its key; without a key the operator writes rules by hand.
    compile_model: str = DEFAULT_COMPILE_MODEL
    model_keys: "ModelKeys" = field(default_factory=lambda: ModelKeys())
    # Live model calls per UTC day across all workspaces; protects the free-tier quota
    # shared with the fallback demo (threat model surface 3).
    model_daily_live_cap: int = 50
    # Of those, how many may draft operator asks (step 8a), so compiles keep the rest.
    model_daily_draft_cap: int = 20

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


@dataclass(frozen=True)
class ModelKeys:
    """The policy compiler's Claude key (D22): the developer's key, and the workspace it must
    name when it has one; either may be absent. Passed with `--env-file`, never read by an
    agent."""

    anthropic: str | None = field(default=None, repr=False)
    anthropic_workspace: str | None = field(default=None, repr=False)


def model_keys_from_env() -> ModelKeys:
    return ModelKeys(
        anthropic=os.environ.get("ANTHROPIC_API_KEY") or None,
        anthropic_workspace=os.environ.get("ANTHROPIC_WORKSPACE_ID") or None,
    )


def database_url_from_env() -> str:
    """What the migration command needs, without the web settings. With
    `DATABASE_PASSWORD_FILE` (the deploy's compose secret), the password comes from that file,
    so it never sits in the environment (plan step 10a)."""
    url = os.environ["DATABASE_URL"]
    password_file = os.environ.get("DATABASE_PASSWORD_FILE")
    if password_file is None:
        return url
    password = Path(password_file).read_text(encoding="utf-8").rstrip("\n")
    return make_conninfo(url, password=password)


def reconcile_log_dir_from_env() -> Path:
    """Where the nightly reconciliation writes its evidence log (plan step 10a): a volume the
    nightly rebuild keeps."""
    return Path(os.environ["RECONCILE_LOG_DIR"])


def from_env() -> Settings:
    return Settings(
        database_url=database_url_from_env(),
        public_origin=os.environ["PUBLIC_ORIGIN"],
        paypal_daily_open_cap=int(os.environ.get("PAYPAL_DAILY_OPEN_CAP", "30")),
        workspace_daily_cap_per_address=int(
            os.environ.get("WORKSPACE_DAILY_CAP_PER_ADDRESS", "20")
        ),
        paypal=paypal_from_env() if "PAYPAL_CLIENT_ID" in os.environ else None,
        run_worker=os.environ.get("RUN_WORKER", "true") != "false",
        compile_model=os.environ.get("COMPILE_MODEL", DEFAULT_COMPILE_MODEL),
        model_keys=model_keys_from_env(),
        model_daily_live_cap=int(os.environ.get("MODEL_DAILY_LIVE_CAP", "50")),
        model_daily_draft_cap=int(os.environ.get("MODEL_DAILY_DRAFT_CAP", "20")),
    )
