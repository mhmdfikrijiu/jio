"""JioFarm — Google AI Pro link hunting endpoints."""

from __future__ import annotations

import json
import re

import requests

from jiofarm.jio.auth import UA

# ------------------------------------------------------------------- constants

JIO_HUNT_ENDPOINTS = [
    ("GET", "https://www.jio.com/api/jio-ott-service/ott/subscription/google-ai"),
    ("GET", "https://www.jio.com/api/jio-ott-service/ott/subscription/google-lead"),
    ("GET", "https://www.jio.com/api/jio-ott-service/ott/subscription/submit"),
    ("GET", "https://www.jio.com/api/jio-ott-service/ott/subscription/activate/googleai"),
]

RECHARGE_PAGES = [
    "https://tiny.jio.com/loginrecharge",
    "https://tiny.jio.com/loginirecharge",
]

LINK_MARKERS = (
    "serviceactivation.google.com",
    "one.google.com/activate-plan",
    "one.google.com/promo",
    "partnerPromotionToken",
)

ASSET_EXTS = (".webp", ".png", ".jpg", ".jpeg", ".svg", ".css", ".js")


# ------------------------------------------------------------------- helpers


def find_google_links(text: str) -> list[str]:
    """Extract Google redeem URLs from a blob of text."""
    if not text:
        return []
    seen: dict[str, None] = {}
    for url in re.findall(r'https?://[^\s"\'<>]+', text):
        u = url.rstrip('\\\'"')
        low = u.lower()
        if low.endswith(ASSET_EXTS) or "myjiostatic.cdn.jio.com" in low:
            continue
        if any(marker in u for marker in LINK_MARKERS):
            seen[u] = None
    return list(seen)


# ------------------------------------------------------------------- main


def hunt_link(s: requests.Session) -> str | None:
    """Hunt for a Google AI Pro redeem link across Jio's subscription APIs.

    Returns the first link found, or None.
    """
    hdr = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.jio.com",
        "Referer": "https://www.jio.com/selfcare/googleai/",
    }

    # 1) subscription endpoints
    for method, url in JIO_HUNT_ENDPOINTS:
        try:
            res = (
                s.get(url, headers=hdr, timeout=15)
                if method == "GET"
                else s.post(url, headers=hdr, json={}, timeout=15)
            )
            if res.status_code != 200:
                continue

            # direct text scan
            direct = find_google_links(res.text)
            if direct:
                return direct[0]

            # JSON key scan
            try:
                data = res.json()
                blob = json.dumps(data)
                for key in ("redirectionURL", "redirectUrl", "url"):
                    v = (
                        data.get(key)
                        or (
                            data.get("data", {}).get(key)
                            if isinstance(data.get("data"), dict)
                            else data.get(key)
                        )
                    )
                    if isinstance(v, str) and any(m in v for m in LINK_MARKERS):
                        return v
                direct = find_google_links(blob)
                if direct:
                    return direct[0]
            except Exception:
                pass
        except Exception:
            continue

    # 2) fallback: recharge pages
    for page in RECHARGE_PAGES:
        try:
            res = s.get(page, headers={"User-Agent": UA}, timeout=15)
            if res.status_code == 200:
                found = find_google_links(res.text)
                if found:
                    return found[0]
        except Exception:
            continue

    return None