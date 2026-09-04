"""JioFarm — Jio subscriber check."""

from __future__ import annotations

import requests

from jiofarm.jio.auth import UA

JIO_CHECK = "https://www.jio.com/api/jio-recharge-service/recharge/mobility/number/{phone}"


def jio_check_subscriber(phone: str) -> bool:
    """Return True if *phone* is a valid Jio subscriber."""
    ok, _ = jio_check_detail(phone)
    return ok


def jio_check_detail(phone: str) -> tuple[bool, str]:
    """Check subscriber status, returning (is_subscribed, detail).

    *detail* is "OK" on success, otherwise Jio's errorMessage
    (e.g. "NOT_SUBSCRIBED_USER") or the transport error.
    """
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
        if res.status_code == 200:
            return True, "OK"
        try:
            detail = res.json().get("errorMessage", res.text[:120])
        except Exception:
            detail = res.text[:120] or f"HTTP {res.status_code}"
        return False, str(detail)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def normalize_phone(raw: str) -> str:
    """Strip non-digits and return the last 10 digits."""
    digits = "".join(c for c in raw if c.isdigit())
    return digits[-10:] if len(digits) > 10 else digits