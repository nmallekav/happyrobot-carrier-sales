"""Server-side rate negotiation. max_rate never leaves this module.

The broker pays the carrier, so the carrier pushes the price up and we hold
the line at max_rate. We start at the loadboard rate and move part of the way
toward max_rate on each counter, never quoting max_rate itself.
"""

MAX_ROUNDS = 3
# How far from loadboard_rate toward max_rate our counter goes on each round.
COUNTER_STEPS = {1: 0.4, 2: 0.7, 3: 0.9}

# In-memory state for now: (mc_number, load_id) -> {"round": int, "our_offer": float, "status": str}
_sessions: dict[tuple[str, str], dict] = {}


def get_session(mc_number: str, load_id: str) -> dict | None:
    return _sessions.get((mc_number, load_id))


def clear_session(mc_number: str, load_id: str) -> None:
    """Delete the negotiation (used when booking fails)."""
    _sessions.pop((mc_number, load_id), None)


def negotiate(mc_number: str, load: dict, carrier_offer: float) -> dict:
    key = (mc_number, load["load_id"])
    loadboard = float(load["loadboard_rate"])
    ceiling = float(load["max_rate"])
    session = _sessions.setdefault(key, {"round": 0, "our_offer": loadboard, "status": "open"})

    if session["status"] != "open":
        return _result(session, "Negotiation for this load is already closed.")

    # Carrier is at or below what we've already offered: take it.
    if carrier_offer <= session["our_offer"]:
        session["status"] = "accepted"
        session["agreed_rate"] = carrier_offer
        return _result(session, "Offer accepted.")

    if session["round"] >= MAX_ROUNDS:
        session["status"] = "rejected"
        return _result(session, "Maximum counter rounds reached. No deal.")

    session["round"] += 1
    counter = round(loadboard + (ceiling - loadboard) * COUNTER_STEPS[session["round"]])

    # Carrier's ask is within our new counter: accept their number.
    if carrier_offer <= counter:
        session["status"] = "accepted"
        session["agreed_rate"] = carrier_offer
        return _result(session, "Offer accepted.")

    session["our_offer"] = counter
    return _result(session, "Counter offer.")


def _result(session: dict, message: str) -> dict:
    return {
        "status": session["status"],  # open | accepted | rejected
        "round": session["round"],
        "rounds_remaining": max(0, MAX_ROUNDS - session["round"]),
        "counter_offer": session["our_offer"] if session["status"] == "open" else None,
        "agreed_rate": session.get("agreed_rate"),
        "message": message,
    }
