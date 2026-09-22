# python-anjoy

A **pure-stdlib** Python toolkit (library + CLI) for the **Anjoy-specific** bits of
stock Anjoy (安佳威视 / Anjoy Vision) IP cameras on Linux — the parts standard
protocols don't cover. Part of the [OpenIPC](https://openipc.org) effort; sibling
of [python-dhip](https://github.com/OpenIPC/python-dhip).

## Scope — what this does and doesn't do

Current-generation Anjoy cameras (e.g. model `MTF45-4G_AF`, firmware V3.4.x) are
standard **ONVIF + RTSP** devices. For device info, PTZ, and video streaming,
**use ONVIF (port 80) and RTSP (port 554)** — any ONVIF client works, and this
repo does not reinvent them (see [`docs/devices.md`](docs/devices.md) for the
confirmed endpoints and the exact RTSP URLs).

python-anjoy focuses on the **vendor-specific glue** ONVIF cannot give you:

- **LAN discovery** — Anjoy's `SYSTEM_SEARCHIPC_MESSAGE` UDP broadcast
  (`anjoy.discover`), which finds cameras on any subnet by MAC/serial/name.
- **The binary AJ protocol** on `comm_server` (TCP 8091) — `XML_TOPSEE` framing,
  plaintext auth, PTZ, and camera-pushed alarms. **Implemented** in `anjoy/comm.py`
  (`AnjoyCommClient`), decoded from a live capture and validated end-to-end against
  an MTF45-4G_AF — see `docs/devices.md`. (`EXECUTE_USER_CMD` uses the same framing;
  wiring it in is a follow-up.)
- **RTSP URL helper** for Anjoy's confirmed query-param + MD5-password scheme
  (`anjoy.rtsp.anjoy_rtsp_url`).

A **legacy** web client for the *older* 2024 firmware generation (SOAP + fixed-key
DES `"WebLogin"` auth) also lives here (`anjoy/soap.py`, `anjoy/client.py`,
`anjoy/aio.py`). It is byte-exact against the vendor `des.js` but **unverified on
hardware**, and the endpoints it uses are **not served on current firmware** (they
fall through to ONVIF). Keep it for older modules; prefer ONVIF elsewhere.

```bash
pip install git+https://github.com/OpenIPC/python-anjoy
```

## Quick start

```bash
anjoy discover                        # find Anjoy cameras on the LAN (vendor probe)
```

```python
from anjoy import discover
from anjoy.rtsp import anjoy_rtsp_url

for dev in discover():
    print(dev["ip"], dev["name"], dev["mac"])

# Confirmed RTSP URL (query-param auth, MD5 password):
url = anjoy_rtsp_url("192.168.0.123", "admin", "123456", stream=0)
# rtsp://192.168.0.123:554/stream0?username=admin&password=E10ADC3949BA59ABBE56E057F20F883E
```

For device control, point an ONVIF client at `http://<host>/onvif/device_service`
(WS-UsernameToken, `admin`/`123456`).

Factory defaults: IP `192.168.0.123/24`, web `admin` / `123456`.

## The two Anjoy generations

| | Older (2024: MC-K45, MC-040-4G, …) | Current (2026: MTF45-4G_AF) |
|---|---|---|
| Standard control | custom web API (SOAP + DES) — legacy module here | **ONVIF** (use any ONVIF client) |
| Video | RTSP | RTSP (`/stream0`, `/stream1`; MD5-password query auth) |
| Discovery | AJ UDP probe (`anjoy.discover`) | AJ UDP probe |
| Vendor extras | binary AJ on `comm_server` 8091 (`anjoy.comm`) | binary AJ on `comm_server` 8091 (`anjoy.comm`) |

## Development

```bash
python -m unittest discover -s tests -v     # deviceless suite
```

`tests/fake_server.py` mocks the (legacy) SOAP API; `anjoy/des.py` is validated
byte-for-byte against the vendor `des.js` (`tests/des_vectors.json`). See
`CLAUDE.md` for conventions and `docs/devices.md` for confirmed hardware behaviour.

## License

MIT © OpenIPC.
