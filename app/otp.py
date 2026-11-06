"""OTP generation and verification (in-memory for now)."""

import secrets
import time

OTP_TTL_SECONDS = 300            # a code must be used within 5 minutes
VERIFIED_TTL_SECONDS = 2 * 3600  # once verified, the MC stays verified for the rest of the call
MAX_ATTEMPTS = 3

# mc_number -> {"code": str, "expires_at": float, "attempts": int, "verified": bool}
_otps: dict[str, dict] = {}


def _purge_expired(keep: str | None = None) -> None:
    """Delete expired entries. `keep` skips one MC so verify() can still
    report "Code expired" for it instead of "No code was sent"."""
    now = time.time()
    for mc in [mc for mc, e in _otps.items() if now > e["expires_at"] and mc != keep]:
        del _otps[mc]


def create(mc_number: str) -> str:
    _purge_expired()
    code = f"{secrets.randbelow(1_000_000):06d}"
    _otps[mc_number] = {
        "code": code,
        "expires_at": time.time() + OTP_TTL_SECONDS,
        "attempts": 0,
        "verified": False,
    }
    return code


def verify(mc_number: str, code: str) -> dict:
    _purge_expired(keep=mc_number)
    entry = _otps.get(mc_number)
    if not entry:
        return {"verified": False, "reason": "No code was sent for this MC number"}
    if time.time() > entry["expires_at"]:
        del _otps[mc_number]
        return {"verified": False, "reason": "Code expired"}
    if entry["attempts"] >= MAX_ATTEMPTS:
        return {"verified": False, "reason": "Too many attempts"}

    entry["attempts"] += 1
    if secrets.compare_digest(entry["code"], code.strip()):
        entry["verified"] = True
        entry["expires_at"] = time.time() + VERIFIED_TTL_SECONDS
        return {"verified": True, "reason": None}
    return {"verified": False, "reason": "Incorrect code",
            "attempts_remaining": MAX_ATTEMPTS - entry["attempts"]}


def is_verified(mc_number: str) -> bool:
    _purge_expired()
    entry = _otps.get(mc_number)
    return bool(entry and entry["verified"])
