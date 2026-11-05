"""Client for the legacy TMS (TCP, line protocol). See docs/tms_protocol.md.

Kept intentionally simple for now: one connection per request, read until
END/ERR, a short timeout, and a few retries for read-only commands.
"""

import os
import socket
from datetime import datetime

TIMEOUT_SECONDS = 5
READ_RETRIES = 3


class TMSError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class BookingUncertain(Exception):
    """LOAD_BOOK reached the TMS but the reply was lost or unreadable
    (timeout, partial or malformed response). The load may or may not be booked."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _clean(value) -> str:
    """Values must not contain | or line breaks."""
    return str(value).replace("|", " ").replace("\r", " ").replace("\n", " ").strip()


def _send(cmd: str, fields: dict) -> list[dict]:
    """Send one request and return the parsed record lines."""
    print("this is cmd", cmd)
    parts = [f"CMD:{cmd}", f"AUTH:{os.environ['TMS_AUTH_TOKEN']}"]
    parts += [f"{k}:{_clean(v)}" for k, v in fields.items() if v not in (None, "")]
    request = "|".join(parts) + "\r\n"

    with socket.create_connection(
        (os.environ["TMS_HOST"], int(os.environ["TMS_PORT"])), timeout=TIMEOUT_SECONDS
    ) as sock:
        sock.sendall(request.encode("ascii"))
        buf = b""
        while True:
            chunk = sock.recv(4096)
            print("this is chunk", chunk)
            if not chunk:
                break
            buf += chunk
            # Stop as soon as the response is complete; don't wait for the close.
            if buf.endswith(b"END\r\n") or (buf.startswith(b"ERR|") and buf.endswith(b"\r\n")):
                break

    text = buf.decode("ascii", errors="replace")
    if text.startswith("ERR|"):
        err = _parse_line(text.strip().removeprefix("ERR|"))
        raise TMSError(err.get("CODE", "UNKNOWN"), err.get("MSG", ""))
    if not text.endswith("END\r\n"):
        print("arreyykya")
        raise TMSError("INCOMPLETE_RESPONSE", "Response ended without END terminator")

    lines = text.split("\r\n")[:-2]  # drop "END" and the trailing ""
    return [_parse_line(line) for line in lines if line]


def _parse_line(line: str) -> dict:
    record = {}
    for pair in line.split("|"):
        key, _, value = pair.partition(":")
        record[key.strip()] = value.strip()
    return record


def _with_retries(cmd: str, fields: dict) -> list[dict]:
    """Retry read-only commands on timeouts / incomplete responses."""
    last_error: Exception | None = None
    for _ in range(READ_RETRIES):
        try:
            return _send(cmd, fields)
        except TMSError as e:
            if e.code != "INCOMPLETE_RESPONSE":
                raise  # a real error from the TMS (e.g. NOT_FOUND) - don't retry
            last_error = e
        except (socket.timeout, OSError) as e:
            last_error = TMSError("TIMEOUT", str(e) or "TMS timed out")
    raise last_error


# ---------- Mapping TMS fields -> our load schema ----------

def _dt(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y%m%d%H%M%S").isoformat()
    except ValueError:
        return value


def _num(value: str | None):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except ValueError:
        return float(value)


def to_load(r: dict) -> dict:
    """Convert a raw TMS record. Includes max_rate - callers must strip it
    before returning data to the carrier/agent."""
    return {
        "load_id": r.get("LOAD_ID"),
        "origin": f"{r.get('ORIG_CITY')}, {r.get('ORIG_STATE')}",
        "destination": f"{r.get('DEST_CITY')}, {r.get('DEST_STATE')}",
        "pickup_datetime": _dt(r.get("PICKUP_DT")),
        "delivery_datetime": _dt(r.get("DELIVERY_DT")),
        "equipment_type": r.get("EQTYPE"),
        "loadboard_rate": _num(r.get("RATE")),
        "max_rate": _num(r.get("MAX_BUY")),
        "weight": _num(r.get("WEIGHT")),
        "commodity_type": r.get("COMMODITY"),
        "num_of_pieces": _num(r.get("PIECES")),
        "miles": _num(r.get("MILES")),
        "dimensions": r.get("DIMS"),
        "notes": r.get("NOTES"),
        "status": r.get("STATUS"),
    }


# ---------- Public operations ----------

def search_loads(orig_city=None, orig_state=None, dest_city=None, dest_state=None,
                 equipment_type=None, pickup_date=None) -> list[dict]:
    records = _with_retries("LOAD_QUERY", {
        "ORIG_CITY": orig_city,
        "ORIG_STATE": orig_state,
        "DEST_CITY": dest_city,
        "DEST_STATE": dest_state,
        "EQTYPE": equipment_type,
        "PICKUP_DATE": pickup_date,  # YYYYMMDD
    })
    return [to_load(r) for r in records]


def get_load(load_id: str) -> dict:
    records = _with_retries("LOAD_GET", {"LOAD_ID": load_id})
    if not records:
        raise TMSError("NOT_FOUND", "Load not found")
    return to_load(records[0])


def book_load(load_id: str, mc_number: str, agreed_rate: float) -> dict:
    """Book once, never retried (a repeat returns ALREADY_BOOKED, not the original success).

    Raises TMSError for a definite ERR reply from the TMS (nothing was booked),
    and BookingUncertain when the outcome is unknown.
    """
    try:
        records = _send("LOAD_BOOK", {
            "LOAD_ID": load_id,
            "MC_NUM": mc_number,
            "AGREED_RATE": agreed_rate,
        })
    except TMSError as e:
        if e.code == "INCOMPLETE_RESPONSE":
            raise BookingUncertain("PARTIAL_RESPONSE") from e
        raise  # ERR|CODE:... from the TMS, e.g. ALREADY_BOOKED, INVALID_RATE
    except OSError as e:  # socket.timeout is an OSError
        raise BookingUncertain("TIMEOUT") from e

    # A reply can end in END and still be malformed, so check the fields we rely on.
    # BOOKING_REF is opaque: only check that it's present, never its format.
    rec = records[0] if len(records) == 1 else {}
    status = rec.get("STATUS", "").upper()
    if rec.get("LOAD_ID") != load_id or not rec.get("BOOKING_REF") or status not in ("PENDING", "BOOKED"):
        raise BookingUncertain("MALFORMED_RESPONSE")

    return {
        "load_id": load_id,
        "booking_ref": rec["BOOKING_REF"],
        "status": status,
        "timestamp_utc": _dt(rec.get("TIMESTAMP")),
    }
