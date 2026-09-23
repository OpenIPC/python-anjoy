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

#### File download / config backup (`SYSTEM_CONTROL`/1023 + `MEDIA_DATA`/2)
The reverse of upload. Captured from AjDevTools "Batch Download Config" and
verified live.

1. **Request** — `SYSTEM_CONTROL_MESSAGE`/`1023`, body
   `<REQUEST_PARAM FileName="/mnt/nand/config.xml" StartPos="0"/>`. The device
   replies `SYSTEM_CONTROL_MESSAGE`/`1023` with
   `<RESPONSE_PARAM Port="8091" Type="1" FileLength="N"/>`.
2. **Data** — `MEDIA_DATA_MESSAGE`/**`2`** frames (download uses code 2; upload
   used 1), body `<POS FileStartPos StartPos DataLen="L"/>` + a 4-byte separator
   + `L` bytes, chunked (~16 KB), ending with `DataLen="0"`.

`AnjoyCommClient.download_file(remote_path)` implements this; `get_config()`
downloads `/mnt/nand/config.xml` — the full `<IPCConfig>` tree (PTZ, encode,
users, network, OSD, alarms…). Verified: 26886-byte config downloaded from an
MTF45-4G_AF. Read-only.

#### Config section write (`SYSTEM_CONFIG_SET_MESSAGE`)
Captured from AjDevTools' per-feature batch buttons (e.g. "Batch Set Title"). The
tool writes **one config section at a time**: `SYSTEM_CONFIG_SET_MESSAGE` carries
the section's numeric `Msg_code` and the section element as the body, and the
device replies with the **same type+code and an empty body** to acknowledge. A
*partial* section is accepted — the device merges it into the stored config.

The write is applied **asynchronously**: the ack returns at once but the change
reaches `/mnt/nand/config.xml` (and `get_config`) a moment later, so pause briefly
before reading it back. Reads use the full-config download (`get_config`) — the
device's per-section GET is not what the vendor tool uses (and returned an empty
body to every request form tried, so it is not relied on).

Confirmed code: **`525` = `MediaConfig/Video/Overlay`** (OSD title + timestamp).
Captured body (title is hex-ASCII, `43616d657261` = "Camera"):

```
SYSTEM_CONFIG_SET_MESSAGE / 525
<Overlay Enable="1" Transparency="0" Style="3" Fontsize="0" Week="1" >
  <TimeOverlay PosX="1" PosY="1" Format="yyyy-mm-dd hh:mm:ss" />
  <TitleOverlay PosX="0" PosY="0" TitleUtf8="43616d657261"/>
</Overlay>
```

`AnjoyCommClient.set_config_section(code, body, confirm=True)` implements this
(codes in `anjoy.const` `CFG_*`). Verified live on MTF45-4G_AF: setting the OSD
title to "Test" then back to "Camera" via code 525 changed and restored
`TitleUtf8` in the downloaded config (asynchronously). Write — `confirm=True`.

Section codes were recovered two ways and cross-checked: **captured live** from
AjDevTools batch dialogs, and **disassembled** from `libtools.so`, where each
`MsgSet*Config` wrapper loads a hard-coded immediate into `r0` before
`MsgSetModuleConfig(code, xml)` — so the wrapper's immediate is the wire code
(`MsgSetVideoOSDConfig` loads 525, matching the live capture). Confirmed live so
far: **`525` = `Overlay`** (OSD title/timestamp), **`228` =
`SystemConfig/MaintainConfig`** (`<MaintainConfig Enable Day Time/>` — auto-reboot;
`Time` stored space-padded, e.g. `" 2: 0: 0"`), **`227` = `SystemConfig/MiscConfig`**
(device language), **`822` = `AlarmConfig/MotionDetectAlarm`** (full element, incl.
`EnableTimeList`/`AlarmAction` children).

Not every section takes a straight write: `TimeConfig` (code 222) did **not**
acknowledge a full-element write (its `NTPConfig` child likely triggers a blocking
NTP re-sync), and AjDevTools' "Batch Sync Time" clock-sync goes over a non-8091
channel (ONVIF/HTTP) — both are left for a dedicated capture.

Typed wrappers over the primitive:

* `set_title(title, confirm=True)` — read-modify-write of `<Overlay>`: only the
  `<TitleOverlay>` title changes (position/font/timestamp/user-OSD preserved).
  `TitleUtf8` (hex of the UTF-8 bytes) is always set, so any title works; the
  legacy `Title` field (hex of the GB2312 bytes) is updated only when the device
  config carries it, and only that path is limited to the GB2312 charset.
* `set_maintenance(enable, day=7, time="HH:MM:SS", confirm=True)` — code 228
  (`day=7` = every day, per the vendor UI).
* `set_language(language, confirm=True)` — code 227 (`"zh_cn"`, `"en"`, …).
* `set_motion(enable, sensitivity=None, alarm_threshold=None, confirm=True)` —
  code 822, read-modify-write of `<MotionDetectAlarm>` (grid/schedule/actions kept).
* `set_person_detect(enable, sensitivity=None, confirm=True)` — code **829**,
  RMW of `<VideoPD>` (AI person detection); verified live (Enable 1↔0).
* `set_face_detect(enable, sensitivity=None, confirm=True)` — code **832**,
  RMW of `<FaceDetect>` (AI face detection); verified live (Enable 0↔1).

The alarm/AI toggles share a `_rmw_section(tag, code, attrs)` helper that
downloads the section, overwrites the named attributes, and writes it back —
preserving every other attribute and child element.

Users — code **223** = `SystemConfig/UserConfig`. Captured from AjDevTools "Batch
Set Password": the tool sends the **plaintext** password and the device computes
the stored `EncryptPwd` (there is no client-side hashing):

```
SYSTEM_CONFIG_SET_MESSAGE / 223
<UserConfig><Account Username="admin" Password="<plaintext>" Group="Administrator" Status="Enable" /></UserConfig>
```

`set_password(password, username="admin", confirm=True)` implements this
(read-modify-write of the target account, so a reset preserves its Group/Status —
no privilege change — and syncs the client's own credential); `get_users()` reads
the accounts (`Username`/`Group`/`Status`/`EncryptPwd`) from the config download.
Verified live on MTF45-4G_AF: changed the admin password and re-authenticated with
it, then reverted. This account is the login for **every** service (binary
control, ONVIF, web), so a change affects them all. Write — `confirm=True`.

Network — code **325** = `NetworkConfig/LANConfig` (`<LANConfig MacAddress DHCP
IPAddress Netmask Gateway DNS1 DNS2 hostname MTU/>`, self-closing).
`set_network(ip=, netmask=, gateway=, dns1=, dns2=, dhcp=, hostname=, mtu=,
confirm=True)` sends a partial `<LANConfig …/>` with only the given fields, which
the device merges (`MacAddress` etc. untouched; no read-back, so rapid successive
calls can't resend stale values while an earlier async write is still landing). Verified live on MTF45-4G_AF: a reversible DNS2 change (IP
preserved), and a **DHCP↔static switch** (static→`DHCP=1`→back to static
`10.216.128.149`). ⚠️ Changing `ip`/`netmask`/`gateway` or enabling `dhcp` can
move the camera — reconnect at, or `anjoy.discovery.discover()`, the new address.
Write — `confirm=True`.

Both verified live with a set-then-restore round-trip. Config attributes are read
back from the full-config download, which formats each attribute on its own line —
parse with a multiline-aware matcher.

#### Reboot (`SYSTEM_CONTROL`/1007)
Captured from AjDevTools "Batch Reboot": a `SYSTEM_CONTROL_MESSAGE`/`1007` with an
empty body reboots the camera. `AnjoyCommClient.reboot()` implements it
(fire-and-forget; the connection drops, reconnect after ~30 s). Verified live.

#### Snapshot (`SYSTEM_CONTROL`/1043 + download)
Captured from AjDevTools "Batch Snap Picture". Trigger
`SYSTEM_CONTROL_MESSAGE`/`1043` with `<REQUEST_PARAM Stream="S" Quality="Q"/>`;
the device saves a JPEG under `/tmp` and replies
`<RESPONSE_PARAM>JpgFile="…"</RESPONSE_PARAM>`, then it is fetched with the file
download above. `AnjoyCommClient.snapshot(stream, quality)` implements this and
returns the JPEG bytes. Verified live: a 640×360 baseline JPEG from an
MTF45-4G_AF. Read-only.

`EXECUTE_USER_CMD` (remote shell) — **CONFIRMED executing on hardware.**
`exec_cmd(*cmds, confirm=True)` builds `<EXECUTE_USER_CMD><CMD DATA="…"/>…>`
(escaped, GB2312-validated) and uploads it. Execution is gated on the **upload
basename**, not a magic file-type: `comm_server` saves the upload to
`/tmp/upfile_*.dat`, then routes by the `FilePath` basename. Recognised
**OEM-default config names** — `defaultconfig.xml`, `config.default.xml`,
`default_2_priority.xml` — are copied into `/mnt/nand/cust/` and processed by
`mainctrl`'s `get_user_cmd_from_xml`, which **runs each command**. An arbitrary
basename is only *stored*.

Verified live on MTF45-4G_AF: uploading `<CMD DATA="killall comm_server"/>` under
`defaultconfig.xml` dropped the control connection mid-upload (the daemon was
actually killed, then respawned by `procman`); the camera stayed healthy (ONVIF
config intact). `exec_cmd` defaults `remote_name="defaultconfig.xml"`.

Side effects: the uploaded file **persists** as the OEM-default in
`/mnt/nand/cust/` and its commands re-run on factory reset (this is exactly the
vendor `ptzClear.xml` behaviour — clear preset memory + restart `comm_server`).
Make commands self-cleaning (end with `rm -f /mnt/nand/cust/<remote_name>`) to
avoid persistence; a command that stops `comm_server` drops the connection as it
runs (expected — reconnect after `procman` respawns it).

> `comm_server` is **single-session**: reconnect too fast after a drop and it may
> not answer until the prior session ages out. Space reconnects; don't brute-force
> it (these Sigmastar units can reboot under probing).
