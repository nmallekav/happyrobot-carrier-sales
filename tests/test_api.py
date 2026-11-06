"""Small API tests: one happy path and two edge cases.

FMCSA and the TMS are faked, so these run offline: `make test`.
"""

import os

# Settings the app reads at import/request time. Set before importing the app.
os.environ.update({
    "API_KEY": "test-key",
    "FMCSA_API_KEY": "test",
    "TMS_HOST": "localhost",
    "TMS_PORT": "1",
    "TMS_AUTH_TOKEN": "test",
})

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import fmcsa, main, negotiation, otp, tms  # noqa: E402

HEADERS = {"X-API-Key": "test-key"}
LOAD = {
    "load_id": "LD1", "origin": "Dallas, TX", "destination": "Phoenix, AZ",
    "equipment_type": "FLATBED", "loadboard_rate": 1000, "max_rate": 1200, "status": "OPEN",
}


@pytest.fixture
def client(monkeypatch):
    otp._otps.clear()
    negotiation._sessions.clear()
    # Fake TMS: one open load, and bookings always succeed.
    monkeypatch.setattr(tms, "search_loads", lambda **kw: [dict(LOAD)])
    monkeypatch.setattr(tms, "get_load", lambda load_id: dict(LOAD))
    monkeypatch.setattr(tms, "book_load", lambda load_id, mc, rate: {
        "load_id": load_id, "booking_ref": "REF1", "status": "PENDING", "timestamp_utc": None})
    return TestClient(main.app)


def fake_fmcsa(monkeypatch, allowed: str, status: str):
    """Make the real FMCSA code path see a carrier with these statuses."""
    monkeypatch.setenv("FMCSA_MOCK", "false")
    body = {"content": [{"carrier": {"allowedToOperate": allowed, "statusCode": status,
                                     "legalName": "TEST CARRIER LLC"}}]}
    monkeypatch.setattr(fmcsa.httpx, "get",
                        lambda url, **kw: httpx.Response(200, json=body, request=httpx.Request("GET", url)))


def test_happy_path_verify_find_and_book(client, monkeypatch):
    fake_fmcsa(monkeypatch, allowed="Y", status="A")

    carrier = client.post("/verify_carrier", json={"mc_number": "123456"}, headers=HEADERS).json()
    assert carrier["eligible"] is True

    verified = client.post("/verify_otp", json={"mc_number": "123456", "code": carrier["otp_code"]},
                           headers=HEADERS).json()
    assert verified["verified"] is True

    loads = client.post("/find_loads", json={"mc_number": "123456", "origin_state": "TX"},
                        headers=HEADERS).json()
    assert [l["load_id"] for l in loads["loads"]] == ["LD1"]
    assert "max_rate" not in loads["loads"][0]  # never sent to the agent

    deal = client.post("/negotiate", json={"mc_number": "123456", "load_id": "LD1", "carrier_offer": 1000},
                       headers=HEADERS).json()
    assert deal["status"] == "booked"
    assert deal["booking"]["booking_ref"] == "REF1"


def test_inactive_carrier_is_rejected(client, monkeypatch):
    # Allowed to operate, but the USDOT registration is inactive: no OTP is sent.
    fake_fmcsa(monkeypatch, allowed="Y", status="I")

    carrier = client.post("/verify_carrier", json={"mc_number": "123456"}, headers=HEADERS).json()
    assert carrier["eligible"] is False
    assert carrier["reason"] == "Carrier's USDOT registration is not active"
    assert "otp_code" not in carrier


def test_find_loads_and_negotiate_require_otp(client):
    # This MC never passed OTP, so neither load search nor negotiation is allowed.
    r = client.post("/find_loads", json={"mc_number": "999999", "origin_state": "TX"}, headers=HEADERS)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "OTP_NOT_VERIFIED"

    r = client.post("/negotiate", json={"mc_number": "999999", "load_id": "LD1", "carrier_offer": 1000},
                    headers=HEADERS)
    assert r.status_code == 403
