# HappyRobot Inbound Carrier Sales: Backend

Backend for a HappyRobot AI voice agent that handles inbound carrier calls for **HappyRobot Logistics**. The agent:

1. checks the carrier's authority with FMCSA,
2. confirms their identity with an OTP,
3. finds a matching load in the legacy TMS,
4. negotiates the rate (never above the confidential max), and
5. books the load before a mocked handoff to a senior rep.

```
HappyRobot voice agent ──HTTPS + X-API-Key──▶ FastAPI backend ──REST──▶ FMCSA API
       (web call)                                (this repo)    ──TCP───▶ Legacy TMS
```

**Live API:** `https://api-production-00029.up.railway.app` (interactive docs at `/docs`)

## Endpoints

Every endpoint requires the `X-API-Key` header.

| Endpoint | Body | What it does |
|---|---|---|
| `POST /verify_carrier` | `{mc_number}` | FMCSA check: allowed to operate **and** USDOT status active. If eligible, creates a 6-digit OTP (valid for 5 minutes, 3 attempts) |
| `POST /verify_otp` | `{mc_number, code}` | Checks the code. A verified MC stays verified for 2 hours |
| `POST /find_loads` | `{mc_number, origin_*, destination_*, equipment_type, pickup_date}` | **Requires a verified OTP.** Searches the TMS and returns up to 3 open loads with full details. `max_rate` is never returned |
| `POST /negotiate` | `{mc_number, load_id, carrier_offer}` | **Requires a verified OTP.** Up to 3 counters, never above `max_rate`. Books the load in the TMS when an offer is accepted |

MC numbers must be digits only, and offers must be greater than 0. Anything else returns a `422`.

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env          # then fill in the values
.venv/bin/uvicorn app.main:app --reload
# http://127.0.0.1:8000/docs
```

| Variable | Purpose |
|---|---|
| `TMS_HOST`, `TMS_PORT`, `TMS_AUTH_TOKEN` | Legacy TMS connection |
| `FMCSA_API_KEY` | FMCSA QCMobile web key |
| `API_KEY` | This backend's key, sent by callers as `X-API-Key` |
| `FMCSA_MOCK` | `true` returns a fake carrier instead of calling FMCSA. FMCSA blocks non-US IPs, so use `true` locally. The deployed app always uses `false` |
| `FMCSA_MOCK_ELIGIBLE` | Whether the mock carrier passes (`true`) or fails (`false`) |

## Test

```bash
make test
```

The tests fake FMCSA and the TMS, so no network is needed. They cover:
- the happy path through all four endpoints
- an inactive carrier being rejected
- OTP being required for `find_loads` and `negotiate`
- counter offers never reaching or revealing `max_rate`

## Deploy (Railway, Docker)

```bash
make deploy     # first time: creates the project and service, copies .env (FMCSA_MOCK=false), deploys, creates a URL
make redeploy   # after code changes: the single deploy command
make env        # after changing .env values (then run make redeploy)
```

Railway builds the `Dockerfile`. The image contains only `requirements.txt` and `app/`; secrets are passed as Railway variables and never included in the image.

## How the hard parts are handled

- **Rate ceiling:** enforced only on the server. Each counter rises by a random 2–5% of the listed rate, and counters stop at a hidden cap set randomly between 85% and 99% of the way to the max. So the max can't be calculated from the counters, and it never appears in a response or the logs.
- **OTP:** created only after FMCSA passes. `find_loads` and `negotiate` reject unverified MC numbers with `403`. The code goes straight to the HappyRobot email step and is never visible to the agent or written to the logs.
- **Unreliable TMS:**
  - Every reply is checked for its `END` terminator and its exact set of fields, so malformed replies are caught even when they end in `END`.
  - `LOAD_QUERY` is retried 3 times (waits of 1 s, 1 s and 2 s).
  - `LOAD_BOOK` is sent once only. If the reply is lost, a `LOAD_GET` confirms whether the booking went through.
- **FMCSA:** only FMCSA's genuine "not found" reply means "MC not found". Any other empty or unexpected reply returns `502`, so a carrier is never wrongly told their MC number doesn't exist.

## Docs

| File | Contents |
|---|---|
| [`docs/steps.md`](docs/steps.md) | Build plan and progress |
| [`docs/edge_case.md`](docs/edge_case.md) | Edge cases per endpoint, plus a flow diagram |
| [`docs/tms_protocol.md`](docs/tms_protocol.md) | Legacy TMS protocol notes, including behaviour observed on the wire |
| [`docs/happyrobot_workflow_prompt.md`](docs/happyrobot_workflow_prompt.md) | Prompt for building the HappyRobot workflow and voice agent |
| [`docs/happyrobot_twin_apps_qa_prompt.md`](docs/happyrobot_twin_apps_qa_prompt.md) | Prompt for Twin logging, the Apps dashboard and the QA test suite |

## Known limitations (proof of concept)

- **State is kept in memory** (OTPs and negotiations), so a restart or redeploy clears it. It runs as a single instance.
- **The OTP goes to an email the caller gives,** not one on file for the carrier. In production it should go to the brokerage's carrier records.
- **The TMS can't tell us which MC number holds a booking,** and a booking reference lost to a timeout can't be recovered.
- **FMCSA has temporarily returned empty data to Railway.** This is handled as a `502`, but a US cloud host proven to reach FMCSA (for example AWS) would be more reliable.
