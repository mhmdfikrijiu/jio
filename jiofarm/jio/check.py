"""JioFarm — Jio subscriber check."""

from __future__ import annotations

import requests

from jiofarm.jio.auth import UA

JIO_CHECK = "https://www.jio.com/api/jio-recharge-service/recharge/mobility/number/{phone}"


def jio_check_subscriber(phone: str) -> bool:
    """Return True if *phone* is a valid Jio subscriber."""
    try:
        res = requests.get(
            JIO_CHECK.format(phone=phone),
            headers={
                "User-Agent": UA,
                "Accept": "application/json",
                "Referer": "https://www.jio.com/",
            },
            timeout=10,
        )
        return res.status_code == 200
    except Exception:
        return False


def normalize_phone(raw: str) -> str:
    """Strip non-digits and return the last 10 digits."""
    digits = "".join(c for c in raw if c.isdigit())
    return digits[-10:] if len(digits) > 10 else digits