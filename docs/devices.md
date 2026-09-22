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
What ONVIF does **not** cover. **Decoded and implemented** in `anjoy/comm.py`
(`AnjoyCommClient`) — captured live from the vendor CameraTestTool and validated
end-to-end against the device.

- **Frame** = magic `58 91 58 51` + 4-byte **little-endian** length + GB2312 XML
  (`<XML_TOPSEE>` envelope). The header may arrive in a separate TCP segment; the
  device **NULL-terminates** its response frames (the length counts the trailing
  `\x00`) — strip it before XML parsing.
- **Auth is plaintext** (`AuthMethod="1"`): `USER_AUTH_MESSAGE`/`CMD_USER_AUTH`
  with `<USER_AUTH_PARAM Username=".." Password=".." AuthMethod="1"/>`; reply is
  `<USER_AUTH_RESPONSE Sessionid="<YYYYMMDDHHMMSS>_<16hex>" Group="Administrator"/>`.
  Every later frame carries that `Sessionid`.
- **PTZ**: `PTZ_CONTROL_MESSAGE`/`PTZ_CMD`, body
  `<xml><cmd>VERB</cmd><panspeed>N</panspeed><tiltspeed>N</tiltspeed></xml>`
  (verbs incl. `zoomtele`/`zoomwide`/`stop`/`PtzRestore`/`PtzReboot`; press-and-hold
  then `stop`).
- Also: `SYSTEM_CONTROL_MESSAGE` (1020 init; 1032 start-stream
  `<REQUEST_PARAM Camera="0" Stream="0"/>`), `AUXPTZ_HEARTBEAT_MESSAGE` keepalive,
  and `ALARM_REPORT_MESSAGE` **pushed by the camera** (e.g. "video Human shape
  detected"). Raw capture: `../MC-F45-4MP-PTZ18x/aj8091-capture.{tx,rx}.bin` in the
  research repo.

#### File upload + EXECUTE_USER_CMD — transport CONFIRMED, execution gated
Captured from AjDevTools "Upload config" and reproduced against a live
MTF45-4G_AF. The file-upload transport is:

1. **Announce** — `SYSTEM_CONTROL_MESSAGE`/`1022`, body
   `<REQUEST_PARAM FileType="0" FilePath="…" FileLength="N" />`. The device replies
   `SYSTEM_CONTROL_MESSAGE`/`1022` with `<RESPONSE_PARAM Port="8091" Type="1" />`.
2. **Data** — one or more `MEDIA_DATA_MESSAGE`/`1` frames, body
   `<POS StartPos="P" DataLen="L" />`, then a **4-byte `00 00 00 00` separator**,
   then `L` raw file bytes.
3. **EOF** — a `MEDIA_DATA_MESSAGE`/`1` with `DataLen="0"`; the device answers
   `SYSTEM_CONTROL_MESSAGE`/`1001` (success — AjDevTools shows "File upload success").

`AnjoyCommClient.upload_file(content, remote_path, file_type=…, confirm=True)`
implements this and is **verified end-to-end** (the live device returns the 1001
success ack).

`EXECUTE_USER_CMD` (remote shell) rides on top: `exec_cmd(*cmds, confirm=True)`
builds `<EXECUTE_USER_CMD><CMD DATA="…"/>…</EXECUTE_USER_CMD>` (escaped,
GB2312-validated) and uploads it. **Execution caveat:** the device parses it via
`get_user_cmd_from_xml` (in `mainctrl`), but only in the **OEM-default-config**
path (`SET_OEM_DEFAULT_CONFIG`). With `file_type=0` (ordinary config) the file is
**stored, not executed** — a live `killall comm_server` uploaded this way did not
restart the process. The `file_type`/target filename that makes the device *run*
the commands (what the vendor's `ptzClear.xml` uses) was **not** pinned down, and
must **not** be brute-forced on hardware — that code path neighbours `CLEARALL`
and `rm /mnt/nand/*`. So `exec_cmd` is guarded (`confirm=True`) and its execution
trigger is the one remaining unknown.

> `comm_server` is **single-session**: reconnect too fast after a drop and it may
> not answer until the prior session ages out. Space reconnects; don't brute-force
> it (these Sigmastar units can reboot under probing).
