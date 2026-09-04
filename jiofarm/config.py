"""JioFarm — centralised configuration from .env and CLI args."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


def _find_env() -> Path:
    """Look for .env next to the project root, falling back to cwd."""
    for base in (Path(__file__).resolve().parent.parent, Path.cwd()):
        candidate = base / ".env"
        if candidate.exists():
            return candidate
    return Path(".env")


def load_config() -> "Config":
    """Load .env and return a populated Config instance."""
    env_path = _find_env()
    load_dotenv(env_path)

    provider = os.getenv("PROVIDER", "grizzlysms").strip().lower()

    if provider == "grizzlysms":
        api_key = os.getenv("GRIZZLY_API_KEY", "").strip()
        if not api_key or api_key == "your_api_key_here":
            msg = (
                "GRIZZLY_API_KEY belum diset di .env\n"
                "  1. Buka https://grizzlysms.com — daftar & topup\n"
                "  2. Copy API key dari dashboard\n"
                "  3. Paste ke .env: GRIZZLY_API_KEY=xxxx"
            )
            raise SystemExit(msg)
        product = os.getenv("GRIZZLY_PRODUCT", "jio").strip()
    elif provider == "fivesim":
        api_key = os.getenv("FIVESIM_API_KEY", "").strip()
        if not api_key or api_key == "your_api_key_here":
            msg = (
                "FIVESIM_API_KEY belum diset di .env\n"
                "  1. Buka https://5sim.net — daftar & topup\n"
                "  2. Copy API key (JWT token) dari dashboard\n"
                "  3. Paste ke .env: FIVESIM_API_KEY=xxxx"
            )
            raise SystemExit(msg)
        product = os.getenv("FIVESIM_PRODUCT", "jiomart").strip()
    else:
        raise SystemExit(f"PROVIDER tidak dikenal: '{provider}'. Pilih: grizzlysms, fivesim")

    return Config(
        provider=provider,
        api_key=api_key,
        product=product,
        max_price=float(os.getenv("MAX_PRICE", "0.47")),
        price_cap=float(os.getenv("PRICE_CAP", "0.5")),
        cancel_delay=float(os.getenv("CANCEL_DELAY_SECONDS", "150")),
        otp_fail_delay=float(os.getenv("OTP_FAIL_DELAY_SECONDS", "420")),
        concurrency=int(os.getenv("CONCURRENCY", "2")),
        db_path=os.getenv("DB_PATH", "results.db"),
        rent_retries=int(os.getenv("RENT_RETRIES", "600")),
        rent_retry_delay=float(os.getenv("RENT_RETRY_DELAY_SECONDS", "5")),
        otp_timeout=int(os.getenv("OTP_TIMEOUT_SECONDS", "600")),
        prefilter_workers=int(os.getenv("PREFILTER_WORKERS", "4")),
        hunt_slots=int(os.getenv("HUNT_SLOTS", "2")),
        tg_bot_token=os.getenv("TG_BOT_TOKEN", "").strip(),
        tg_chat_id=os.getenv("TG_CHAT_ID", "").strip(),
    )


@dataclass
class Config:
    """All runtime configuration, sourced from .env and overridden by CLI args."""

    provider: str = "grizzlysms"
    api_key: str = ""
    product: str = "jio"  # GrizzlySMS: "jio", 5SIM: "jiomart"
    max_price: float = 0.47
    price_cap: float = 0.5
    cancel_delay: float = 150.0
    otp_fail_delay: float = 420.0
    concurrency: int = 2
    db_path: str = "results.db"
    rent_retries: int = 600
    rent_retry_delay: float = 5.0
    otp_timeout: int = 600
    prefilter_workers: int = 4
    hunt_slots: int = 2

    # telegram (optional)
    tg_bot_token: str = ""
    tg_chat_id: str = ""

    # runtime overrides (set by CLI)
    max_price_override: float | None = field(default=None, repr=False)
    target: int | None = field(default=None, repr=False)
    duration: float | None = field(default=None, repr=False)
    count: int = field(default=1, repr=False)
    concurrency_override: int | None = field(default=None, repr=False)

    @property
    def effective_max_price(self) -> float | None:
        raw = self.max_price_override if self.max_price_override is not None else self.max_price
        if raw is None:
            return None
        return min(raw, self.price_cap)

    @property
    def effective_concurrency(self) -> int:
        return self.concurrency_override or self.concurrency

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.tg_bot_token and self.tg_chat_id)