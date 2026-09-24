# python-anjoy — contributor & architecture guide

Pure-stdlib toolkit for the **Anjoy-specific** bits of stock Anjoy IP cameras.
Sibling of python-dhip; same layering discipline. MIT, no runtime dependencies.

**Scope (see README + docs/devices.md).** Current-gen Anjoy (e.g. MTF45-4G_AF) is
a standard ONVIF+RTSP device — do NOT reimplement device/PTZ/stream control here;
use ONVIF. This repo owns the vendor-only glue: LAN discovery (AJ UDP probe) and
the binary AJ protocol on comm_server:8091 (capture-gated). The SOAP/DES modules
(soap.py/client.py/aio.py) are a LEGACY client for the older firmware
generation — validated against des.js but unverified on hardware, and not served
on current firmware. Keep them; don't grow them.

## Layering (do not collapse)

```
des.py        fixed-key DES-ECB("WebLogin") auth  -> hex userid/passwd
soap.py       HTTP-SOAP transport: envelope, request()/call()/extract()/check_response()
client.py     AnjoyClient (sync): one thin method per operation over self.soap.call
aio.py        AsyncAnjoyClient: asyncio mirror; imports const/des/extract/check_response
comm.py       binary AJ protocol (comm_server 8091) — capture-gated, WIP
events.py     ALARM_REPORT listener over comm.py — WIP
discovery.py  UDP 36584->3001 GB2312 probe
rtsp.py       rtsp:// URL builder + ffmpeg helpers
firmware.py   ANJOY888 parse + /setFirmwareUpgrade — capture-gated, WIP
const.py      ports, endpoints, PTZ vocab/preset map, AJ Msg_types, errors
exceptions.py AnjoyError -> AnjoyAPIError(code) -> LoginError
cli.py        argparse over AnjoyClient -> the `anjoy` console entry point
```

## Wire contract (keep sync and async identical)

- SOAP envelope, DES auth, `extract()` (unwrap) and `check_response()` (raise on
  error) live in `soap.py` / `des.py` and are **imported** by `aio.py` — never
  reimplemented. If the wire form changes, it changes in one place.
- `request()` returns raw text and never raises on an API-level error; `call()`
  runs `check_response()` then `extract()`. Mirrors python-dhip.
- Auth is stateless (fixed key), so there is no login/session/keep-alive — unlike
  python-dhip's DHIP. Do not add one for the SOAP path.

## Confirmed vs. unconfirmed

`const.py` values come from the stock web-UI JS, the vendor SDK/tools, and a
device config dump. What still needs a live capture is flagged in `README.md`
("Status") and in the plan: exact `Content-Type`, response shapes, RTSP path,
the binary framing/auth on 8091, `EXECUTE_USER_CMD`'s carrier, and firmware
upload. When you confirm one, drop the caveat and add a test.

## DES auth is validated, not guessed

`anjoy/des.py` reproduces the vendor `des.js` **byte-for-byte**; the oracle is
`tests/des_vectors.json`, generated once via `node des.js`. If you touch
`des.py`, `python -m unittest tests.test_des` must stay green. To regenerate
vectors you need the vendor `des.js` + node (see the anjoy research repo).

## Adding a new endpoint/method

1. Add the endpoint path constant to `const.py` (grouped by area).
2. Add a thin method to `AnjoyClient` (`self.call(const.X, body)`); build any
   `<xml>…</xml>` body with `client._xml(...)`.
3. Mirror it in `AsyncAnjoyClient` (`aio.py`).
4. Wire a CLI subcommand in `cli.py` if user-facing.
5. Add a test in `tests/` driving it against `FakeAnjoyHTTPServer` (assert the
   request body + response parsing). Keep the suite deviceless.

## Tests

`python -m unittest discover -s tests -v`. `tests/fake_server.py` speaks the
SOAP contract (parses the DES auth header, records `(endpoint, body)`, dispatches
a per-test handler map). No device or network needed.

## Safety

- Never auto-run destructive ops. `reboot`, firmware upload, and (future)
  `EXECUTE_USER_CMD` must be explicit/guarded.
- The "WebLogin" DES key is obfuscation, not security — say so, don't imply the
  header protects anything.
