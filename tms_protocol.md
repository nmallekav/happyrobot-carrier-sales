# Legacy TMS Protocol

> Source: the "field manual" provided with the HappyRobot FDE challenge. It is **not a formal spec**. Where behavior is not stated, **the wire is authoritative**: confirm behavior against the live server.
>
> Connection details are in `.env`: `TMS_HOST`, `TMS_PORT`, `TMS_AUTH_TOKEN`.

---

## Overview

The Legacy TMS exposes a line-oriented request/response protocol over TCP. It predates structured payloads at HappyRobot and stays in service for operational continuity.

## Transport

| Property | Value |
|----------|-------|
| Transport | TCP |
| Encoding | ASCII |
| Line terminator | `\r\n` |
| Maximum frame size | 4096 bytes (including terminator) |
| Idle timeout | 30 seconds (server-initiated close) |
| Connection lifecycle | **One request per connection** |

- The server **closes the connection after writing the response**.
- Clients must **open a fresh connection for each request**.
- Connection reuse is not supported. The server's behavior if you reuse a connection is implementation-defined.

## Request Framing

A request is a **single line** terminated by `\r\n`, made of `|`-delimited `KEY:VALUE` pairs.

```
CMD:<command>|AUTH:<token>|<FIELD>:<VALUE>|...\r\n
```

- Field names are **UPPER CASE**.
- `CMD` must appear **first**.
- `AUTH` is **required on every request**.
- Values must **not** contain `|` or `\r\n`.
- Unknown fields are accepted and silently discarded.

## Response Framing

There are two response shapes.

**Success:** zero or more record lines, then a terminator line.

```
<FIELD>:<VALUE>|<FIELD>:<VALUE>|...\r\n
...
END\r\n
```

**Error:** a single line.

```
ERR|CODE:<code>|MSG:<msg>\r\n
```

- Fixed-width field values are **space-padded on the right**, so trim them when parsing.
- Field widths are **not documented**. Work them out by counting from sample transcripts.

## Authentication

- Every request must carry `AUTH:<token>`.
- A missing or invalid token returns `AUTH_FAILED`, and the server closes the connection.
- Tokens are candidate-scoped and issued through the candidate portal.

## Commands

| Command | Purpose |
|---------|---------|
| `LOAD_QUERY` | Search the open load board |
| `LOAD_GET` | Retrieve the full record for a single load |
| `LOAD_BOOK` | Commit a booking against a load |
| `DEBUG_ECHO` | Diagnostic round-trip. **Bypasses fault injection.** |

> The manual does not document the request fields each command takes. Discover them on the wire (`MISSING_FIELD` errors should reveal the required fields).

## Command Details (discovered on the wire, 2026-11-04)

### `LOAD_QUERY`
At least one filter is required (`MISSING_FIELD: At least one filter required`).

| Filter | Format / notes |
|--------|----------------|
| `ORIG_CITY` | City name. Matches on a **substring** (`X` matched "Phoeni**x**") |
| `ORIG_STATE` | 2-letter code (`IL`) |
| `DEST_CITY` | City name, substring match |
| `DEST_STATE` | 2-letter code. Invalid values return `MISSING_FIELD: Invalid DEST_STATE` |
| `EQTYPE` | `DRY_VAN`, `REEFER`, `FLATBED`, `STEP_DECK`, `POWER_ONLY`. Other values return `Invalid EQTYPE` |
| `PICKUP_DATE` | `YYYYMMDD` |

Each record is a summary: `LOAD_ID, ORIG_CITY, ORIG_STATE, ORIG_ZIP, DEST_CITY, DEST_STATE, DEST_ZIP, PICKUP_DT, EQTYPE, RATE, MILES, STATUS`. It does **not** include `MAX_BUY`.

### `LOAD_GET`
Requires `LOAD_ID` (for example `LD01102`). An unknown ID returns `ERR|CODE:NOT_FOUND`.

Sample:
```
LOAD_ID:LD01102     |ORIG_CITY:Billings                      |ORIG_STATE:MT|ORIG_ZIP:59101|DEST_CITY:Phoenix                       |DEST_STATE:AZ|DEST_ZIP:85004|PICKUP_DT:20261108050400|DELIVERY_DT:20261109000400|EQTYPE:FLATBED   |RATE:1683    |WEIGHT:7482    |COMMODITY:Frozen Goods                  |PIECES:5     |MILES:873   |DIMS:51ft x 8ft x 9ft              |NOTES:Coil load. Coil racks required.   ...   |STATUS:OPEN    |MAX_BUY:1981    
END
```

### `LOAD_BOOK`
Commits a booking against a load. Requires `LOAD_ID`, `MC_NUM` and `AGREED_RATE` (a missing one returns `MISSING_FIELD`).

**Bookings are keyed to the requesting token.** The same load can be in different booking states for different tokens. This is intentional and not documented further. Our backend uses a single `TMS_AUTH_TOKEN` for every carrier, so `ALREADY_BOOKED` means *our* token already booked this load (for this carrier or another one), not a booking by somebody outside our system.

**A token's view is monotonic.** Once `ALREADY_BOOKED` is seen for a `LOAD_ID` on a token, every later attempt on that token returns `ALREADY_BOOKED`. Other tokens are unaffected. Every booking made while testing permanently uses up that load for our token.

Success:
```
> CMD:LOAD_BOOK|AUTH:<token>|LOAD_ID:LD0000045821|MC_NUM:872144|AGREED_RATE:2200
< LOAD_ID:LD0000045821|BOOKING_REF:BR00000000091277|STATUS:BOOKED  |TIMESTAMP:20260504193122
< END
```
- `BOOKING_REF` is server-assigned and opaque. Store it, but do not parse it.
- `TIMESTAMP` is UTC, `YYYYMMDDHHMMSS`.
- `STATUS` is fixed-width and right-padded (`BOOKED  `), so trim it.
- The response does **not** include `MAX_BUY`, so passing it through cannot leak `max_rate`.

Errors:

| Case | Response |
|------|----------|
| Repeat booking, even with the **same** MC and rate | `ERR\|CODE:ALREADY_BOOKED\|MSG:load not available` |
| Rate rejected | `ERR\|CODE:INVALID_RATE\|MSG:rate rejected` |
| Unknown load | `ERR\|CODE:UNKNOWN_LOAD\|MSG:load not found` (unlike `LOAD_GET`, which returns `NOT_FOUND`) |
| Missing field | `ERR\|CODE:MISSING_FIELD\|MSG:missing required field` |

- **A repeat booking is not idempotent.** It returns `ALREADY_BOOKED`, never the original success, so a `BOOKING_REF` lost to a timeout or a partial response **cannot be recovered**.
- **`INVALID_RATE` covers more than the obvious cases** (for example `0`). The accepted range for `AGREED_RATE` is not published. Operators are expected to work out the bounds by observation.
#### Observed on the wire (2026-11-05, load `LD01108`, now permanently booked for our token)

| # | Request | Response |
|---|---------|----------|
| 1 | `LOAD_GET` before booking | `STATUS:OPEN` |
| 2 | `LOAD_BOOK`, MC `111111`, rate 529 | `LOAD_ID:LD01108\|BOOKING_REF:C4FZQ57IGH23L92Z\|STATUS:PENDING \|TIMESTAMP:20261105082719` then `END` |
| 3 | `LOAD_BOOK` again, **same** MC `111111` | `ERR\|CODE:ALREADY_BOOKED\|MSG:Already booked by this token` |
| 4 | `LOAD_BOOK`, **different** MC `222222` | First attempt: injected **timeout**. Retry: `ERR\|CODE:ALREADY_BOOKED\|MSG:Already booked by this token` (identical to #3) |
| 5 | `LOAD_GET` after booking | Full record with `STATUS:PENDING`. **No MC field.** (This response was also *malformed*: `C\|XXOMMODITY:` inside an otherwise valid line ending in `END`.) |
| 6 | `LOAD_QUERY EQTYPE:DRY_VAN` after booking | **The load is still listed**, with `STATUS:PENDING` |

What this differs from or adds to the published transcripts:
- **A successful booking returns `STATUS:PENDING`**, not `BOOKED`. Accept both as success.
- **`BOOKING_REF` is a 16-character alphanumeric string** (`C4FZQ57IGH23L92Z`), not `BR…`. It is opaque either way.
- **The TMS cannot tell us which carrier holds a booking.** Same MC and different MC get byte-identical `ALREADY_BOOKED` responses, and `LOAD_GET` has no MC field. Our backend must record which MC it booked each load for.
- **`LOAD_GET` shows whether our token has booked a load**: the `STATUS` changes from `OPEN` to `PENDING`. That is a read-only way to settle an ambiguous `LOAD_BOOK`.
- **`LOAD_QUERY` still returns booked loads.** Filter search results to `STATUS:OPEN` before pitching them.
- **A response that ends in `END` can still be malformed.** Checking for the terminator alone is not enough; field names must be validated too.

### `DEBUG_ECHO`
```
CMD:DEBUG_ECHO|AUTH:<token>|MSG:hello
→ ECHO|AUTH:OK|FIELDS_PARSED:3|MSG:hello
  END
```

### TMS → load schema mapping

| TMS field | Our field |
|-----------|-----------|
| `LOAD_ID` | `load_id` |
| `ORIG_CITY`, `ORIG_STATE` (`ORIG_ZIP`) | `origin` |
| `DEST_CITY`, `DEST_STATE` (`DEST_ZIP`) | `destination` |
| `PICKUP_DT` (`YYYYMMDDHHMMSS`) | `pickup_datetime` |
| `DELIVERY_DT` (`YYYYMMDDHHMMSS`) | `delivery_datetime` |
| `EQTYPE` | `equipment_type` |
| `RATE` | `loadboard_rate` |
| `MAX_BUY` | `max_rate` (**confidential**) |
| `WEIGHT` | `weight` |
| `COMMODITY` | `commodity_type` |
| `PIECES` | `num_of_pieces` |
| `MILES` | `miles` |
| `DIMS` | `dimensions` (free text, for example `51ft x 8ft x 9ft`) |
| `NOTES` | `notes` |
| `STATUS` | `status` (for example `OPEN`) |

## Error Codes

Codes seen in practice (**the list is not exhaustive**, so adapters must handle unknown codes):

| Code | Likely meaning |
|------|----------------|
| `AUTH_FAILED` | Missing or invalid token |
| `UNKNOWN_CMD` | Unrecognized `CMD` |
| `MISSING_FIELD` | Required field not supplied |
| `UNKNOWN_LOAD` | `load_id` not found (returned by `LOAD_BOOK`) |
| `NOT_FOUND` | `load_id` not found (returned by `LOAD_GET`; observed but not in the manual) |
| `ALREADY_BOOKED` | Load already booked |
| `INVALID_RATE` | Rate rejected on booking |
| `MALFORMED` | Request could not be parsed |
| `SERVER_ERROR` | Internal or transient failure |

## Fault Behavior

The server **injects faults on operational commands** (`LOAD_QUERY`, `LOAD_GET`, `LOAD_BOOK`). Fault injection is a feature of the LTMS non-production environment and is **not advertised in the response**. The operator is expected to detect the condition from the wire.

### Categories observed in the field

| Fault | What happens on the wire |
|-------|--------------------------|
| **Timeout** | The server accepts the connection and reads the request, but **never writes a response**. The idle timeout eventually closes the connection. |
| **Partial response** | The server writes a **prefix of a valid response**, then closes **without the `END` terminator**. The last record may be syntactically incomplete. |
| **Malformed response** | The response **violates the framing rules**, for example extra delimiters, unterminated lines, or field values that exceed their declared widths. |
| **Delayed termination** | The server writes a **complete response** (including `END`) but **holds the connection open** after the point where the client expects it to close. |

> This list is **descriptive, not normative**. The server may add new fault categories without notice.

### Detection

- **Faults are not signaled.** There is no fault code, no marker field, and no out-of-band notification.
- A timeout looks like a slow connection.
- A partial response looks like a successful response that happened to be short.
- **The operator's contract is with the wire, not with the server.**
- **`DEBUG_ECHO` does not pass through the fault layer.** It **cannot** be used to probe fault behavior or to confirm that the operational path is healthy.

---

## Adapter Implementation Notes

**Connection and reading**
- Open a new TCP connection per request.
- Set a short connect timeout and a **read deadline** well under the server's 30s idle timeout. Don't wait for the server to close the connection.
- **Stop reading as soon as `END\r\n` or an `ERR|...` line arrives**, then close the socket yourself. This handles *delayed termination*.
- Enforce the 4096-byte frame limit. Abort and treat the response as malformed if it is exceeded.

**Validating responses (fault detection)**
- **Success requires `END\r\n`.** If the connection closes before `END`, it is a *partial response*, so fail and retry. Never return partial records.
- Every record line must parse cleanly as `KEY:VALUE` pairs with known field names. Reject extra or empty delimiters, lines without a terminator, and values over the expected width.
- A read deadline that expires with no complete response is a *timeout*.
- Validate field contents, such as numeric rates and miles and parseable datetimes, before handing data to the agent.
- Treat any unknown response shape as a fault, since new categories can appear.

**Retries**
- Retry `LOAD_QUERY` / `LOAD_GET` on timeout, partial, malformed, or `SERVER_ERROR`, with bounded attempts (for example 3) and short backoff. The total time must fit a live voice call.
- **Be careful retrying `LOAD_BOOK`.** It is not idempotent: a repeat returns `ALREADY_BOOKED`, not the original success. Only an *ambiguous* failure (timeout, partial or malformed response) is worth retrying. `ALREADY_BOOKED` on that retry means our earlier attempt most likely went through, because bookings are scoped to our token, but the `BOOKING_REF` is lost. Never retry `INVALID_RATE`, `UNKNOWN_LOAD` or `MISSING_FIELD`.
- When retries run out, return a clean, typed error to the agent so it can tell the carrier gracefully.

**General**
- Sanitize values by stripping `|`, `\r` and `\n` before building a request.
- Right-trim every field value. Map TMS field names to the load schema in `CLAUDE.md`.
- Never log `AUTH` tokens.
- Log every fault detected, with its category, command and attempt number, for QA metrics and the audit trail.
- **Don't use `DEBUG_ECHO` as a health check for operational commands.** It only proves that connectivity and auth work.
