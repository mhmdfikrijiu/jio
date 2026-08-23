"""JioFarm — Jio selfcare authentication helpers."""

from __future__ import annotations

import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

JIO_SEND_OTP = "https://www.jio.com/api/jio-login-service/login/sendOtp"
JIO_VALIDATE_OTP = "https://www.jio.com/api/jio-login-service/login/validateOtp"


def jio_send_otp(s: requests.Session, phone: str) -> bool:
    """POST to Jio sendOtp endpoint. Returns True on success."""
    res = s.post(
        JIO_SEND_OTP,
        json={
            "mobileNumber": phone,
            "loginFlowType": "MOBILE",
            "alternateNumber": "",
        },
        timeout=10,
    )
    return res.status_code == 200


def jio_validate_otp(s: requests.Session, otp: str) -> bool:
    """POST to Jio validateOtp endpoint. Returns True on success."""
    res = s.post(JIO_VALIDATE_OTP, json={"otp": otp}, timeout=10)
    return res.status_code == 200