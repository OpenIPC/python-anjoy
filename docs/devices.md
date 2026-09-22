# Confirmed device behaviour (live)

## MC-F45 — model `MTF45-4G_AF` (ONVIF `ONVIF_ICAMERA`)

Confirmed on a live sample (2026-09-22), firmware **V3.4.5.6 (build 2026-01-06)**,
serial `EF0000000BF45324`.

### Open ports
| Port | Service |
|---|---|
| 80/tcp | gSOAP/2.8 — **ONVIF** (device/media/ptz/imaging) + static web UI |
| 554/tcp | RTSP |
| 8091/tcp | `comm_server` — the binary AJ protocol (vendor-specific) |

Closed on this unit: 443, 5000 (binary DHIP), 8000 (Hik shim), 37777 (Dahua shim),
12351 (h5live), 1935 (rtmp), 3001.

### Standard control = ONVIF + RTSP (use existing tooling)
This generation is a standard **ONVIF** camera. All of the following are verified
working with WS-UsernameToken (PasswordDigest) auth, `admin`/`123456`:

- `GetDeviceInformation`, `GetSystemDateAndTime` (the latter needs no auth)
- `GetProfiles` → tokens `MainStream`, `SubStream`
- `GetStreamUri`, PTZ `GetStatus` / `ContinuousMove` (pan/tilt **and** the optical
  zoom) / `Stop`

**RTSP** (from ONVIF `GetStreamUri`):

```
rtsp://<host>:554/stream0?username=<user>&password=<UPPER(md5(password))>   # MainStream: HEVC 2560x1440@25 + PCM mulaw
rtsp://<host>:554/stream1?username=<user>&password=<UPPER(md5(password))>   # SubStream:  HEVC  640x360@25
```

i.e. RTSP auth is via query params, `password` = uppercase MD5 hex of the account
password.

> The custom web-UI endpoints referenced in this firmware's JS — `/ipcLogin`
> (SOAP + DES `"WebLogin"`), `/login` + Dahua RPC2, `/getPtzConfig` etc. — are
> **not** served as distinct handlers here: every POST path falls through to the
> ONVIF gSOAP dispatcher and faults. That web-UI code is generic and inactive on
> this generation. The DES/SOAP client under `anjoy/` targets the *older* 2024
> firmware generation (MC-K45 etc.) and is unverified on hardware.

### Vendor-specific = the binary AJ protocol on 8091 (`comm_server`)
This is what ONVIF does **not** cover (e.g. `EXECUTE_USER_CMD`, factory config).
Live probe: port 8091 accepts a TCP connection, sends no banner, and resets the
connection on guessed framing (raw XML, LE/BE length-prefixed XML all RST). The
exact framing + `USER_AUTH` handshake must be captured from `AjDevTools` /
`CameraTestTool` driving a unit (`tcpdump`) before `anjoy/comm.py` can implement
it. Do not brute-force 8091 against hardware — these Sigmastar units can reboot
under probing.
