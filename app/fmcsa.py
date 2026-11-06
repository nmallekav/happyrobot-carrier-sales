"""FMCSA QCMobile API: look up a carrier by MC (docket) number."""

import os

import httpx

BASE_URL = "https://mobile.fmcsa.dot.gov/qc/services"


class FMCSAUnavailable(Exception):
    """FMCSA answered, but not with a usable result (we can't tell found from not found)."""


def _shape(data) -> str:
    """Describe a reply's structure for logs without including any values."""
    if isinstance(data, dict):
        return "{" + ", ".join(f"{k}: {type(v).__name__}"
                               + (f"[{len(v)}]" if isinstance(v, list) else "")
                               for k, v in data.items()) + "}"
    return type(data).__name__


def _flag(name: str) -> bool:
    return os.environ.get(name, "false").strip().lower() == "true"


def _mock_verify(mc: str) -> dict:
    """Fake FMCSA result for local testing (FMCSA blocks non-US IPs).
    FMCSA_MOCK_ELIGIBLE=true -> carrier passes, false -> carrier is blocked."""
    if _flag("FMCSA_MOCK_ELIGIBLE"):
        return {
            "mc_number": mc,
            "eligible": True,
            "reason": None,
            "legal_name": "MOCK CARRIER LLC",
            "dba_name": None,
            "dot_number": 1234567,
            "allowed_to_operate": "Y",
            "status_code": "A",
            "mock": True,
        }
    return {
        "mc_number": mc,
        "eligible": False,
        "reason": "Carrier is not authorized to operate",
        "legal_name": "MOCK CARRIER LLC",
        "dba_name": None,
        "dot_number": 1234567,
        "allowed_to_operate": "N",
        "status_code": "I",
        "mock": True,
    }


def verify_mc(mc_number: str) -> dict:
    mc = "".join(ch for ch in mc_number if ch.isdigit())
    if _flag("FMCSA_MOCK"):
        return _mock_verify(mc)

    resp = httpx.get(
        f"{BASE_URL}/carriers/docket-number/{mc}",
        params={"webKey": os.environ["FMCSA_API_KEY"]},
        headers={"Accept": "application/json"},
        timeout=10,
    )
    if resp.is_error:
        # Debug output (the API key is masked).
        print("FMCSA request:", str(resp.request.url).replace(os.environ["FMCSA_API_KEY"], "***"))
        print("FMCSA request headers:", dict(resp.request.headers))
        print("FMCSA status:", resp.status_code)
        print("FMCSA response headers:", dict(resp.headers))
        print("FMCSA response body:", resp.text)
    resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError:
        print("FMCSA reply was not JSON")
        raise FMCSAUnavailable("FMCSA reply was not JSON")

    content = data.get("content") if isinstance(data, dict) else None

    # FMCSA's genuine "no such carrier" reply is exactly {"content": [], "retrievalDate": "..."}
    # (checked from AWS CloudShell with MC 200000).
    if content == [] and "retrievalDate" in data:
        return {"mc_number": mc, "eligible": False, "reason": "MC number not found"}

    # Anything else without a carrier record is not proof the MC doesn't exist,
    # so report "can't verify" (502) instead of "not found".
    if not (isinstance(content, list) and content and isinstance(content[0], dict)
            and isinstance(content[0].get("carrier"), dict)):
        print("FMCSA unexpected reply shape:", _shape(data))
        raise FMCSAUnavailable("FMCSA returned no usable carrier data")

    carrier = content[0]["carrier"]
    # Both must hold: FMCSA allows them to operate AND their USDOT registration is active.
    # Real carriers have come back allowedToOperate=Y with statusCode=I (inactive).
    allowed = carrier.get("allowedToOperate") == "Y"
    active = carrier.get("statusCode") == "A"
    if not allowed:
        reason = "Carrier is not authorized to operate"
    elif not active:
        reason = "Carrier's USDOT registration is not active"
    else:
        reason = None
    return {
        "mc_number": mc,
        "eligible": allowed and active,
        "reason": reason,
        "legal_name": carrier.get("legalName"),
        "dba_name": carrier.get("dbaName"),
        "dot_number": carrier.get("dotNumber"),
        "allowed_to_operate": carrier.get("allowedToOperate"),
        "status_code": carrier.get("statusCode"),
    }
