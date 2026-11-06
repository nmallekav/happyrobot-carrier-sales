# CLAUDE.md

## Instructions for Claude

- **Follow [`docs/steps.md`](./docs/steps.md) as the build plan.** Work through it step by step. Use it to decide what to work on next, and check items off (`- [x]`) when they are done.
- This file holds the project context and requirements. If `steps.md` and this file ever disagree, the **requirements in this file win**.
- Ask before making any architectural decision that conflicts with the hard requirements below.

---

## Project Summary

This is the **HappyRobot FDE Technical Challenge: Inbound Carrier Sales Automation**.

The project is a proof of concept for **HappyRobot Logistics**, a mid-sized freight brokerage in Chicago, IL. They have about 2,000 active carriers and move about 500 loads per week across dry van, refrigerated, and flatbed.

The goal is to automate the first leg of an inbound carrier call with a HappyRobot AI voice agent. The agent verifies the carrier, matches a load, negotiates the rate, and does a mocked handoff to a senior rep, all without a dispatcher.

### Customer pain points

- **Missed calls and long hold times** at peak hours (Monday morning, Friday afternoon), so carriers go to competing brokers.
- **Inconsistent rate negotiation.** Dispatchers apply different ceilings, which leaks margin.
- **Manual FMCSA verification**, which is slow and error-prone.
- **No audit trail** of what was said, offered, or agreed on each call.
- **Dispatcher burnout** from repetitive, low-complexity calls.

---

## Target Call Flow

1. **Carrier calls in** through a HappyRobot **web call trigger** (no phone number is provisioned).
2. **Credential check:** collect the MC number and verify active operating authority with the **FMCSA API**. If the check fails, end the call.
3. **Identity confirmation (OTP):** send an OTP by SMS or email and validate it **before** load matching. This is a hard compliance requirement.
4. **Load search:** query the legacy TMS by lane preference and equipment type, then pitch the matching load.
5. **Rate negotiation:** the carrier can accept, reject, or counter. The agent allows **up to 3 counter rounds** and never goes above `max_rate`.
   - If a rate is agreed, tentatively book the load.
   - If there's no deal after 3 rounds, close the call professionally and log it as a **failed negotiation**. Do **not** transfer.
6. **Handoff:** mock a transfer to the senior rep queue. Live transfers don't work with web calls.
7. **Logging:** record the carrier MC, load ID, agreed rate, outcome, and notes in Twin.

---

## Load Fields (from TMS)

| Field | Description |
|-------|-------------|
| `load_id` | Unique identifier |
| `origin` | Starting location |
| `destination` | Delivery location |
| `pickup_datetime` | Pickup date/time |
| `delivery_datetime` | Delivery date/time |
| `equipment_type` | Equipment needed |
| `loadboard_rate` | Listed rate on load boards |
| `max_rate` | Broker's ceiling. **Never disclose to the carrier.** |
| `weight` | Weight in lbs |
| `commodity_type` | Type of goods |
| `num_of_pieces` | Number of items |
| `miles` | Distance |
| `dimensions` | L x W x H in inches |
| `notes` | Additional notes |

---

## Systems and Dependencies

| System | Role | Access |
|--------|------|--------|
| **Legacy TMS** | Source of truth for loads | Raw **TCP socket**, fixed-width line protocol (AS/400 style). No REST or JSON. Hostname, port, and auth token are provided separately. |
| **FMCSA API** | Carrier authority verification | Public REST API, looked up by MC number |
| **HappyRobot Platform** | Voice agent, workflow orchestration, data layer (Twin), UI (Apps), SMS | Full access |
| **Senior Rep Queue** | Handoff target | **Mocked** |

### Credentials and configuration

- Secrets live in **`.env`**, which is gitignored and must never be committed or printed in logs. `.env.example` is the committed template.
- Variables: `TMS_HOST`, `TMS_PORT`, `TMS_AUTH_TOKEN`, `FMCSA_API_KEY`, `API_KEY` (our backend's own key, sent as `X-API-Key`), `FMCSA_MOCK`, `FMCSA_MOCK_ELIGIBLE`.
- **FMCSA blocks requests from India** (the user's location): it returns a 403 from AWS ELB, while the same request returns 200 from AWS CloudShell. For local development, set `FMCSA_MOCK=true`. `FMCSA_MOCK_ELIGIBLE=true/false` decides whether the mock carrier passes or is blocked. In the US deployment, set `FMCSA_MOCK=false`.
- Load them from environment variables in code. Never hardcode them.

### Legacy TMS notes

- **Protocol reference: [`docs/tms_protocol.md`](./docs/tms_protocol.md).** Read it before working on the TMS adapter.
- **Fault handling: see the [Fault Behavior](./docs/tms_protocol.md#fault-behavior) and [Adapter Implementation Notes](./docs/tms_protocol.md#adapter-implementation-notes) sections of `docs/tms_protocol.md`.** The server silently injects timeouts, partial responses, malformed responses, and delayed termination. The adapter must detect every one of these from the wire and must also be tested against them (Step 7).

- It has three operations: **load search**, **load detail retrieval**, and **load booking**.
- The only documentation is the protocol spec provided with the challenge.
- **It is unreliable under load.** Expect intermittent timeouts and malformed responses. The integration layer **must** handle these gracefully with timeouts, retries, response validation, and clear errors back to the agent.

---

## Hard Requirements

- **Rate ceiling:** enforce `max_rate` on the server side. Never reveal it to the carrier, directly or indirectly. That means no hinting, no "close to our max", and never returning it in tool responses the agent can read.
- **OTP:** send it after FMCSA passes and before load matching. It must **resist social engineering**, with no bypass however the carrier frames the request.
- **Authentication:** every backend endpoint requires auth.
- **Data layer:** capture all call activity in **HappyRobot Twin**. Use an external DB only if Twin can't support a requirement, and document why.
- **Operational UI:** build a **HappyRobot Apps** dashboard for the operations manager that doesn't rely on raw platform logs. Use an external UI only if Apps can't do the job, and document why.
- **QA:** define North Star KPIs and write a scripted test suite covering standard, edge-case, and adversarial scenarios. Document the results.
- **Deployment:** containerize with **Docker** so it deploys to a cloud environment with a **single command**.
- **Carrier experience:** carriers are often on the road, so the agent must be direct, efficient, and conversational.

---

## Backend (current state)

The code is in `app/`: `main.py` (FastAPI routes), `tms.py`, `fmcsa.py`, `otp.py` and `negotiation.py`. State is **in memory** for now. Every route requires the `X-API-Key` header, which must match `API_KEY` in `.env`.

There are **exactly four endpoints**, one per HappyRobot tool. Keep the tool count minimal and don't add endpoints without asking the user.

| Method | Path | Body | Purpose |
|--------|------|------|---------|
| POST | `/verify_carrier` | `{mc_number}` | FMCSA check. If eligible, also generates an OTP and returns `otp_code` for the HappyRobot SMS step |
| POST | `/verify_otp` | `{mc_number, code}` | Checks the code (5 min expiry, 3 attempts) |
| POST | `/find_loads` | `{mc_number, origin_city, origin_state, destination_city, destination_state, equipment_type, pickup_date}` (`mc_number` required; search fields optional, at least one required) | Requires a verified OTP (403 otherwise). TMS search plus full details for the top 3 matches. Never includes `max_rate` |
| POST | `/negotiate` | `{mc_number, load_id, carrier_offer}` | Requires a verified OTP (403 otherwise). Up to 3 counters, enforced on the server. On accept it **books the load in the TMS** and returns `status: "booked"` |

Run it locally:
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload
# interactive docs at http://127.0.0.1:8000/docs
```

Approach: **keep things simple first.** The user verifies the happy path before we harden the error cases.

## Tech Stack

- **Backend:** Python + FastAPI
- **Integrations:** FMCSA REST API, TMS over TCP sockets
- **Platform:** HappyRobot (workflow, voice agent, Twin, Apps, SMS)
- **Deployment:** Docker, single-command cloud deploy

---

## Deliverables

1. Summary email to the prospect covering what was built and the next steps
2. Build description document for the brokerage's IT and business review
3. A **private** code repository (reviewers will be invited)
4. HappyRobot platform workflow link
5. A walkthrough video of about 5 minutes covering the live demo, dashboard, and QA results
