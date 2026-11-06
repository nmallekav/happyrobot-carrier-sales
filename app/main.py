"""Carrier sales backend for the HappyRobot voice agent.

Four tools, matching the call flow:
  1. verify_carrier  - FMCSA check, then generate an OTP if the carrier passes
  2. verify_otp      - check the code the carrier reads back
  3. find_loads      - search the TMS and return full details for the top matches
  4. negotiate       - counter / accept / reject; books the load on accept
"""

import os
import secrets

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

load_dotenv()

from app import fmcsa, negotiation, otp, tms  # noqa: E402  (needs env loaded first)

MAX_LOADS_RETURNED = 3
MAX_DETAIL_LOOKUPS = 5  # cap LOAD_GET calls per search so the caller isn't left waiting

app = FastAPI(title="HappyRobot Carrier Sales API", version="0.2.0")


def require_api_key(x_api_key: str = Header(default="")):
    if not secrets.compare_digest(x_api_key, os.environ["API_KEY"]):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


auth = [Depends(require_api_key)]

# Request field types. Anything else is rejected by pydantic with a 422.
MCNumber = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\d+$")]   # digits only
OTPCode = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\d{6}$")]  # exactly 6 digits


def public_load(load: dict) -> dict:
    """Never send max_rate to the agent/carrier."""
    return {k: v for k, v in load.items() if k != "max_rate"}


def tms_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except tms.TMSError as e:
        status = 404 if e.code in ("NOT_FOUND", "UNKNOWN_LOAD") else 502
        raise HTTPException(status_code=status, detail={"code": e.code, "message": e.message})


# ---------- 1. Verify carrier (FMCSA + send OTP) ----------

class VerifyCarrierRequest(BaseModel):
    mc_number: MCNumber


@app.post("/verify_carrier", dependencies=auth)
def verify_carrier(body: VerifyCarrierRequest):
    try:
        result = fmcsa.verify_mc(body.mc_number)
    except httpx.HTTPStatusError as e:
        # Don't echo the URL: it contains the FMCSA API key.
        raise HTTPException(status_code=502, detail=f"FMCSA lookup failed (HTTP {e.response.status_code})")
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="FMCSA lookup failed (network error)")

    if result["eligible"]:
        # The HappyRobot workflow sends this code to the carrier via its SMS step.
        result["otp_code"] = otp.create(result["mc_number"])
        result["otp_expires_in_seconds"] = otp.OTP_TTL_SECONDS
    return result


# ---------- 2. Verify OTP ----------

class VerifyOtpRequest(BaseModel):
    mc_number: MCNumber
    code: OTPCode


@app.post("/verify_otp", dependencies=auth)
def verify_otp(body: VerifyOtpRequest):
    return otp.verify(body.mc_number, body.code)


# ---------- 3. Find loads (search + details) ----------

class FindLoadsRequest(BaseModel):
    origin_city: str | None = None
    origin_state: str | None = None
    destination_city: str | None = None
    destination_state: str | None = None
    equipment_type: str | None = None  # DRY_VAN, REEFER, FLATBED, STEP_DECK, POWER_ONLY
    pickup_date: str | None = None  # YYYYMMDD


@app.post("/find_loads", dependencies=auth)
def find_loads(body: FindLoadsRequest):
    matches = tms_call(
        tms.search_loads,
        orig_city=body.origin_city,
        orig_state=body.origin_state.upper() if body.origin_state else None,
        dest_city=body.destination_city,
        dest_state=body.destination_state.upper() if body.destination_state else None,
        equipment_type=body.equipment_type.upper().replace(" ", "_") if body.equipment_type else None,
        pickup_date=body.pickup_date,
    )
    # LOAD_QUERY also returns loads we've already booked (STATUS:PENDING); only pitch open ones.
    matches = [m for m in matches if (m.get("status") or "").upper() == "OPEN"]

    # Search results are summaries without weight, notes, etc. Only pitch loads with full
    # details: if a lookup fails, skip that load and try the next match.
    loads = []
    for summary in matches[:MAX_DETAIL_LOOKUPS]:
        if len(loads) == MAX_LOADS_RETURNED:
            break
        try:
            loads.append(public_load(tms.get_load(summary["load_id"])))
        except (tms.TMSError, OSError):
            continue

    # 200 with no loads must only ever mean "nothing matches". If loads exist but we
    # couldn't get details for any of them, that's a TMS problem, not "no loads".
    if matches and not loads:
        raise HTTPException(
            status_code=502,
            detail={"code": "LOAD_DETAILS_UNAVAILABLE",
                    "message": "Matching loads exist but their details could not be retrieved"},
        )
    return {"total_matches": len(matches), "loads": loads}


# ---------- 4. Negotiate (books on accept) ----------

class NegotiateRequest(BaseModel):
    mc_number: MCNumber
    load_id: str
    carrier_offer: float = Field(gt=0)  # 0 or less -> 422


@app.post("/negotiate", dependencies=auth)
def negotiate(body: NegotiateRequest):
    load = tms_call(tms.get_load, body.load_id)
    print("this is load", load)
    result = negotiation.negotiate(body.mc_number, load, body.carrier_offer)

    if result["status"] == "accepted":
        result["booking"] = book(load, body.mc_number, result["agreed_rate"])
        result["status"] = "booked"
        result["next_step"] = "Handoff to senior rep (mocked)"
    return result


BOOKING_ERROR_STATUS = {"ALREADY_BOOKED": 409, "UNKNOWN_LOAD": 404, "INVALID_RATE": 422}


def book(load: dict, mc_number: str, agreed_rate: float) -> dict:
    """Book the load. `load` is the record fetched before negotiating."""
    load_id = load["load_id"]
    try:
        return tms.book_load(load_id, mc_number, agreed_rate)

    except tms.TMSError as e:
        # The TMS said no: nothing was booked. Drop the negotiation and report the error.
        negotiation.clear_session(mc_number, load_id)
        raise HTTPException(
            status_code=BOOKING_ERROR_STATUS.get(e.code, 502),
            detail={"code": e.code, "message": e.message},
        )

    except tms.BookingUncertain as e:
        # The reply was lost. Don't retry LOAD_BOOK; check the load once instead.
        # PENDING/BOOKED means the booking went through.
        try:
            after = tms.get_load(load_id)
        except tms.TMSError:
            after = {}
        if (after.get("status") or "").upper() in ("PENDING", "BOOKED"):
            return {
                "load_id": load_id,
                "booking_ref": None,  # only sent in the lost reply; can't be recovered
                "status": after["status"].upper(),
                "timestamp_utc": None,
                "note": f"Booking confirmed via LOAD_GET after {e.reason}; booking reference unavailable",
            }

        negotiation.clear_session(mc_number, load_id)
        raise HTTPException(
            status_code=500,
            detail={"code": "BOOKING_UNCONFIRMED", "reason": e.reason,
                    "message": "Could not confirm the booking with the TMS"},
        )
