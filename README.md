# python-anjoy

A **pure-stdlib** Python client (library + CLI) for managing **stock** Anjoy
(安佳威视 / Anjoy Vision) IP cameras from Linux. Sibling of
[python-dhip](https://github.com/OpenIPC/python-dhip) (Dahua). Part of the
[OpenIPC](https://openipc.org) camera-integration effort.

Anjoy modules are Sigmastar (Infinity6 / Mercury6) IP cameras with a web control
API and a binary control protocol. This client speaks the **web SOAP-over-HTTP
API** by default — the tractable surface — and also finds devices on the LAN and
builds RTSP URLs for live video. No third-party dependencies (ffmpeg is only
needed for the optional RTSP recording helpers).

```bash
pip install git+https://github.com/OpenIPC/python-anjoy
```

## Quick start

```bash
anjoy discover                                  # find cameras on the LAN
anjoy 192.168.0.123 info                        # version + chip UUID + app type
anjoy 192.168.0.123 ptz right --speed 5         # start moving; then:
anjoy 192.168.0.123 ptz stop
anjoy 192.168.0.123 ptz zoom tele
anjoy 192.168.0.123 preset goto 94              # PtzReboot preset (resets serial path)
anjoy 192.168.0.123 config /getPtzConfig        # any GET endpoint
anjoy 192.168.0.123 call /getSystemVersionInfo  # raw endpoint escape hatch
```

```python
from anjoy import AnjoyClient, discover

for dev in discover():
    print(dev["ip"], dev["name"], dev["mac"])

cam = AnjoyClient("192.168.0.123", "admin", "123456")
print(cam.info())
cam.ptz_move("right"); cam.ptz_stop()
cam.preset_goto(94)

# async mirror
from anjoy.aio import AsyncAnjoyClient
# await AsyncAnjoyClient("192.168.0.123").info()
```

Factory defaults: IP `192.168.0.123/24`, web `admin` / `123456`.

## How it talks to the camera

An Anjoy camera exposes **two** control surfaces plus a UDP discovery probe:

| | Web SOAP API (this client) | Binary AJ protocol |
|---|---|---|
| Transport | HTTP POST, port **80** | `XML_ANJVISION`/`XML_TOPSEE` over TCP **8091** (`comm_server`) |
| Body | SOAP envelope, XML/JSON replies | GB2312 XML message envelope |
| Auth | DES(`"WebLogin"`) hex in SOAP header | `USER_AUTH_MESSAGE` (`EncryptPwd`, scheme WIP) |
| Status | implemented | capture-gated (`anjoy/comm.py`, WIP) |

### Discovery

Broadcast a `SYSTEM_SEARCHIPC_MESSAGE` from UDP source port `36584` to
`255.255.255.255:3001`; cameras answer regardless of subnet with their IP, MAC,
serial and (hex-encoded) name. Needs `CAP_NET_RAW`/root when pinning a NIC.

### SOAP auth — a fixed key

Every SOAP call carries the credentials DES-encrypted with the **hard-coded key
`"WebLogin"`**, hex-encoded, in the header — there is no challenge or session:

```
userid = hex( DES_ECB_encrypt("WebLogin", username) )   # zero-padded to 8
passwd = hex( DES_ECB_encrypt("WebLogin", password) )
```

```xml
<?xml version="1.0"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2001/12/soap-envelope">
  <soap:Header><userid>HEX</userid><passwd>HEX</passwd></soap:Header>
  <soap:Body> <xml><cmd>right</cmd>…</xml> </soap:Body>
</soap:Envelope>
```

`anjoy.des` reimplements this exactly, validated byte-for-byte against the
vendor's `des.js` (see `tests/des_vectors.json`). **The key is published in the
firmware — this is obfuscation, not security.**

## Endpoint ↔ method map (subset)

| Method | Endpoint |
|---|---|
| `get_version()` | `/getSystemVersionInfo` |
| `get_chip_uuid()` | `/get_mstar_chip_uuid` |
| `get_config(ep)` / `set_config(ep, xml)` | any `/get*Config` / `/set*` |
| `ptz_move(dir, speed)` / `ptz_stop()` | `/setPTZCmd` |
| `ptz_zoom/focus/iris(...)` | `/setPTZCmd` |
| `preset_set/goto/del(n)` / `get_presets()` | `/PresetList`, `/getPresetList` |
| `get_ptz_config()` | `/getPtzConfig` |
| `get_users()` | `/getUserConfig` |
| `reboot()` | `/getSystemControlString` |
| `call(ep, body)` | anything (escape hatch) |

Full endpoint catalogue is in `anjoy/const.py`.

### PTZ

Serial-PTZ factory default is **PELCO-D, address 1, 2400 8N1**. Move directions:
`up/down/left/right` + diagonals (`left_up`…), press-and-hold then `stop`. Lens:
`zoom_tele/zoom_wide`, `focus_near/focus_far`, `iris_open/iris_close`. Some preset
numbers are feature triggers (cruise 65–68, scan bounds 92/93, `PtzReboot` 94 —
handy to test the serial path). Presets are remappable via `/setPtzAdvanceConfig`.

## Status

Implemented and unit-tested (deviceless): discovery, DES auth, SOAP transport
(sync + async), device info / config / PTZ / presets / users / reboot, RTSP URL
building. **Confirmed on a live sample** during bring-up: the exact
`Content-Type` the device wants, precise response shapes, and the RTSP path
template. **Work in progress (capture-gated):** the binary AJ protocol
(`anjoy/comm.py`) incl. `EXECUTE_USER_CMD`, the alarm-event listener, and
firmware upload (`ANJOY888` container).

## Development

```bash
python -m unittest discover -s tests -v     # deviceless suite
```

The `tests/fake_server.py` `FakeAnjoyHTTPServer` mocks the device, so the whole
suite runs with no camera and no network egress. See `CLAUDE.md` for conventions
and the "adding a new endpoint" checklist.

## License

MIT © OpenIPC.
