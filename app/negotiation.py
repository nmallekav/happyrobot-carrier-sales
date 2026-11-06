"""Server-side rate negotiation. max_rate never leaves this module.

The broker pays the carrier, so the carrier pushes the price up and we hold the
line below max_rate. We start at the loadboard rate. Each counter goes up by a
random step based on the loadboard rate (never on max_rate), so the counters
carry no information about the ceiling. Counters also stop at a hidden cap that
is set randomly per negotiation somewhere below max_rate, so even a carrier who
asks for a huge amount can't push us to a fixed point that reveals the max.
"""

import math
import secrets

MAX_ROUNDS = 3
STEP_RANGE = (0.02, 0.05)  # each counter rises by 2-5% of the loadboard rate (random)
CAP_RANGE = (0.85, 0.99)   # hidden cap: 85-99% of the way from loadboard to max_rate (random)

_rng = secrets.SystemRandom()  # unpredictable, so the randomness can't be reproduced

# In-memory state for now:
# (mc_number, load_id) -> {"round", "our_offer", "cap", "status", "agreed_rate"}
_sessions: dict[tuple[str, str], dict] = {}


def get_session(mc_number: str, load_id: str) -> dict | None:
    return _sessions.get((mc_number, load_id))


def clear_session(mc_number: str, load_id: str) -> None:
    """Delete the negotiation (used when booking fails)."""
    _sessions.pop((mc_number, load_id), None)


def _new_session(loadboard: float, max_rate: float) -> dict:
    gap = max(0.0, max_rate - loadboard)
    cap = loadboard + gap * _rng.uniform(*CAP_RANGE)
    return {"round": 0, "our_offer": loadboard, "cap": cap, "status": "open"}


def _next_counter(session: dict, loadboard: float) -> float:
    step = loadboard * _rng.uniform(*STEP_RANGE)
    counter = min(session["our_offer"] + step, session["cap"])
    counter = math.floor(counter / 5) * 5          # round down to a natural $5 figure
    return max(counter, session["our_offer"])      # never go backwards


def negotiate(mc_number: str, load: dict, carrier_offer: float) -> dict:
    key = (mc_number, load["load_id"])
    loadboard = float(load["loadboard_rate"])
    max_rate = float(load["max_rate"])
    session = _sessions.setdefault(key, _new_session(loadboard, max_rate))

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
    counter = _next_counter(session, loadboard)

    # Carrier's ask is within our new counter: accept their number.
    # counter <= cap < max_rate, so an accepted rate never exceeds max_rate.
    if carrier_offer <= counter:
        session["status"] = "accepted"
        session["agreed_rate"] = carrier_offer
        return _result(session, "Offer accepted.")

    session["our_offer"] = counter
    return _result(session, "Counter offer.")


def _result(session: dict, message: str) -> dict:
    # Never include "cap" or anything derived from max_rate.
    return {
        "status": session["status"],  # open | accepted | rejected
        "round": session["round"],
        "rounds_remaining": max(0, MAX_ROUNDS - session["round"]),
        "counter_offer": session["our_offer"] if session["status"] == "open" else None,
        "agreed_rate": session.get("agreed_rate"),
        "message": message,
    }
