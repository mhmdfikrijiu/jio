"""JioFarm — 5SIM.net API client.

5SIM API v1 reference:
- Base: https://5sim.net/v1
- Auth: Bearer token in Authorization header
- Buy:  GET /user/buy/activation/{country}/{operator}/{product}
- SMS:  GET /user/check/{id}
- Cancel: GET /user/cancel/{id}
"""

from __future__ import annotations

import threading
import time

import requests


class FiveSimError(Exception):
    pass


class FiveSim:
    """Minimal client for the 5SIM.net API."""

    BASE = "https://5sim.net/v1"
    MIN_GAP = 1.0  # seconds between API calls

    def __init__(self, api_key: str):
        self.key = api_key
        self._lock = threading.Lock()
        self._last = 0.0

    # ----------------------------------------------------------------- low level
    def _get(self, path: str, retries: int = 3) -> dict | str:
        url = f"{self.BASE}{path}"
        headers = {
            "Authorization": f"Bearer {self.key}",
            "Accept": "application/json",
        }
        for attempt in range(1, retries + 1):
            with self._lock:
                wait = self.MIN_GAP - (time.time() - self._last)
                if wait > 0:
                    time.sleep(wait)
                self._last = time.time()
            try:
                res = requests.get(url, headers=headers, timeout=20)
                if res.status_code == 429:
                    back = attempt * 3
                    time.sleep(back)
                    continue
                res.raise_for_status()
                ct = res.headers.get("content-type", "")
                if "json" in ct:
                    return res.json()
                return res.text.strip()
            except requests.RequestException:
                if attempt == retries:
                    raise
                time.sleep(2)
        raise FiveSimError("5SIM request failed after retries.")

    # ---------------------------------------------------------------- high level
    def balance(self) -> float:
        """Return account balance in USD."""
        data = self._get("/user/profile")
        if isinstance(data, dict):
            return float(data.get("balance", 0))
        raise FiveSimError(f"balance error: {data}")

    def rent(self, max_price: float | None = None) -> tuple[str, str]:
        """Buy an activation (GrizzlySMS-compatible alias for buy)."""
        return self.buy()

    def buy(self, country: str = "india", operator: str = "any",
            product: str = "jiomart") -> tuple[str, str]:
        """Buy an activation. Returns (activation_id, phone_number)."""
        path = f"/user/buy/activation/{country}/{operator}/{product}"
        data = self._get(path)
        if isinstance(data, dict):
            aid = str(data.get("id", ""))
            phone = str(data.get("phone", ""))
            if aid and phone:
                return aid, phone
        raise FiveSimError(f"buy error: {data}")

    def ready(self, act_id: str) -> None:
        """5SIM auto-activates — no-op."""

    def complete(self, act_id: str) -> None:
        """5SIM auto-completes — no-op."""

    def status(self, act_id: str) -> tuple[str, str | None]:
        """GrizzlySMS-compatible alias for check()."""
        raw_status, code = self.check(act_id)
        # Map 5SIM statuses to GrizzlySMS format
        MAP = {
            "PENDING": "WAIT",
            "RECEIVED": "OK",
            "TIMEOUT": "CANCEL",
            "CANCELED": "CANCEL",
            "BANNED": "CANCEL",
            "FINISHED": "OK",
        }
        mapped = MAP.get(raw_status, "WAIT")
        return mapped, code

    def check(self, act_id: str) -> tuple[str, str | None]:
        """Check for SMS. Returns (status, code_or_None).

        Status values: 'PENDING', 'RECEIVED', 'TIMEOUT', 'CANCELED', 'BANNED'
        """
        data = self._get(f"/user/check/{act_id}")
        if isinstance(data, dict):
            status = str(data.get("status", "PENDING"))
            sms_list = data.get("sms", [])
            code = None
            if sms_list and isinstance(sms_list, list):
                code = sms_list[0].get("code") if isinstance(sms_list[0], dict) else None
            return status, code
        return "PENDING", None

    def cancel(self, act_id: str) -> None:
        """Cancel an activation (refund)."""
        self._get(f"/user/cancel/{act_id}")


def poll_sms(client: FiveSim, act_id: str, timeout: int = 120, interval: int = 5) -> str:
    """Poll 5SIM until the SMS arrives or the deadline expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, code = client.check(act_id)
        if status == "RECEIVED" and code:
            return code
        if status in ("CANCELED", "TIMEOUT", "BANNED"):
            raise FiveSimError(f"activation {status}")
        time.sleep(interval)
    raise FiveSimError(f"SMS timeout after {timeout}s")