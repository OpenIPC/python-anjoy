"""Binary AJ protocol client — ``comm_server`` on TCP 8091.

This is the vendor-specific control channel ONVIF does not cover. The wire format
was captured live from the vendor CameraTestTool driving an MTF45-4G_AF and is:

* **Frame** = 4-byte magic ``58 91 58 51`` + 4-byte little-endian length + that
  many bytes of GB2312-encoded XML (an ``<XML_TOPSEE>`` envelope). The header may
  arrive in a separate TCP segment from the body.
* **Envelope**::

      <?xml version="1.0" encoding="GB2312" ?>
      <XML_TOPSEE>
      <MESSAGE_HEADER Msg_type=".." Msg_code=".." Msg_channel="0" Msg_flag="0" Sessionid=".."/>
      <MESSAGE_BODY> .. </MESSAGE_BODY>
      </XML_TOPSEE>

* **Auth** is plaintext: send ``USER_AUTH_MESSAGE`` /``CMD_USER_AUTH`` with
  ``<USER_AUTH_PARAM Username=".." Password=".." AuthMethod="1"/>`` (Sessionid
  empty); the reply carries ``<USER_AUTH_RESPONSE Sessionid=".." Group=".."/>``.
  Every later frame echoes that Sessionid.
* **PTZ** rides ``PTZ_CONTROL_MESSAGE`` /``PTZ_CMD`` with the body
  ``<xml><cmd>VERB</cmd><panspeed>N</panspeed><tiltspeed>N</tiltspeed></xml>``
  (verbs: up/down/left/right/…, zoomtele/zoomwide, PtzRestore, PtzReboot, stop).
* The camera **pushes** ``ALARM_REPORT_MESSAGE`` frames on the same connection.
"""

from __future__ import annotations

import socket
import re
import struct
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape as _xml_escape
from typing import Iterator

from . import const
from .exceptions import AnjoyError, LoginError

MAGIC = b"\x58\x91\x58\x51"
# Sanity cap so a bogus/hostile length field cannot force a huge alloc.
MAX_FRAME = 16 * 1024 * 1024
# Upload basenames the device processes as an OEM-default config and whose
# embedded EXECUTE_USER_CMD it actually RUNS (confirmed on MTF45-4G_AF).
EXEC_TRIGGER_NAMES = ("defaultconfig.xml", "config.default.xml",
                      "default_2_priority.xml")
_XML_DECL = '<?xml version="1.0" encoding="GB2312" ?>'


def _attr(value) -> str:
    """Escape a value for use as an XML attribute (& < > \")."""
    return _xml_escape(str(value), {'"': '&quot;'})


def build_envelope(msg_type: str, msg_code: str, body: str = "",
                   sessionid: str = "", channel: int = 0) -> bytes:
    # Structure mirrors the vendor's captured frames: a leading newline, the
    # header on one line, and (when present) the body wrapped in newlines. The
    # device is sensitive to this shape — a bare XML declaration is ignored.
    header = (f'<MESSAGE_HEADER Msg_type="{_attr(msg_type)}" '
              f'Msg_code="{_attr(msg_code)}" Msg_channel="{_attr(channel)}" '
              f'Msg_flag="0" Sessionid="{_attr(sessionid)}"  />')
    body_el = f"<MESSAGE_BODY>\n{body}\n</MESSAGE_BODY>" if body else "<MESSAGE_BODY/>"
    doc = f"\n{_XML_DECL}\n<XML_TOPSEE>\n{header}\n{body_el}\n</XML_TOPSEE>\n"
    return doc.encode("gb2312", "replace")


def _exec_body(commands) -> str:
    """Build the ``<EXECUTE_USER_CMD>`` body, escaping each command and refusing
    any that is not GB2312-encodable (the wire encoding) — a lossy ``?`` could
    silently change a shell argument or become an unquoted wildcard."""
    items = []
    for c in commands:
        try:
            str(c).encode("gb2312")
        except UnicodeEncodeError as e:
            raise AnjoyError(f"command is not GB2312-encodable: {c!r}") from e
        items.append(f'<CMD DATA="{_attr(c)}" />')
    return f"<EXECUTE_USER_CMD>{''.join(items)}</EXECUTE_USER_CMD>"


def build_frame(xml: bytes) -> bytes:
    return MAGIC + struct.pack("<I", len(xml)) + xml


def _message_body(xml: bytes) -> bytes:
    """Return the inner bytes of ``<MESSAGE_BODY>`` (``b""`` when empty or
    self-closing). Used to tell a bare ack from an error/status payload."""
    if re.search(rb"<MESSAGE_BODY\s*/>", xml):
        return b""
    m = re.search(rb"<MESSAGE_BODY>(.*?)</MESSAGE_BODY>", xml, re.S)
    return m.group(1).strip() if m else b""


class AnjoyCommClient:
    """A single ``comm_server`` (TCP 8091) connection: framing + plaintext auth."""

    def __init__(self, host: str, user: str = const.DEFAULT_USER,
                 password: str = const.DEFAULT_PASSWORD,
                 port: int = const.COMM_PORT, timeout: float = const.DEFAULT_TIMEOUT):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.user = user
        self.password = password
        self.sock: socket.socket | None = None
        self.sessionid = ""
        self.group: str | None = None
        self._buf = b""
        self._pending_len: int | None = None

    # -- connection ---------------------------------------------------------
    def connect(self) -> None:
        self.close()          # drop any prior connection + session state first
        self.sock = socket.create_connection((self.host, self.port), self.timeout)

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None
        self.sessionid = ""
        self.group = None
        self._buf = b""
        self._pending_len = None

    def __enter__(self):
        if self.sock is None:
            self.connect()
        try:
            self.login()
        except Exception:
            self.close()
            raise
        return self

    def __exit__(self, *exc):
        self.close()

    # -- framing ------------------------------------------------------------
    def _send(self, msg_type: str, msg_code: str, body: str = "",
              channel: int = 0) -> None:
        assert self.sock is not None, "not connected"
        xml = build_envelope(msg_type, msg_code, body, self.sessionid, channel)
        self.sock.sendall(build_frame(xml))

    def recv_frame(self, strip_null: bool = True) -> tuple[str, bytes]:
        """Read one framed message; return (msg_type, raw_xml_bytes)."""
        # Resumable across read timeouts: a partial header stays in ``_buf`` and
        # continues on the next call; once the header is parsed its length is
        # remembered in ``_pending_len`` so a timeout mid-body does not restart
        # header parsing from the buffered body bytes.
        if self._pending_len is None:
            header = self._recv_exact(8)
            if header[:4] != MAGIC:
                raise AnjoyError(f"bad AJ magic {header[:4].hex()} (expected 58915851)")
            length = struct.unpack("<I", header[4:8])[0]
            if length > MAX_FRAME:
                raise AnjoyError(f"AJ frame length {length} exceeds cap {MAX_FRAME}")
            self._pending_len = length
        xml = self._recv_exact(self._pending_len)
        self._pending_len = None
        # The device NULL-terminates its frames (the length counts the trailing
        # \x00); strip it or ElementTree rejects "junk after document element".
        # File-download data frames carry raw bytes after the envelope, so their
        # reader passes strip_null=False to keep trailing NULs intact.
        if strip_null:
            xml = xml.rstrip(b"\x00")
        mt = ""
        try:
            root = ET.fromstring(xml.decode("gb2312", "replace"))
            hdr = root.find("MESSAGE_HEADER")
            if hdr is not None:
                mt = hdr.get("Msg_type", "")
        except ET.ParseError:
            m = re.search(rb'Msg_type="([^"]+)"', xml)
            if m:
                mt = m.group(1).decode("ascii", "replace")
        return mt, xml

    def _recv_exact(self, n: int) -> bytes:
        assert self.sock is not None, "not connected"
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise AnjoyError("comm_server closed the connection")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    # -- auth ---------------------------------------------------------------
    def login(self) -> str:
        """Send the plaintext USER_AUTH and capture the session id."""
        body = (f'<USER_AUTH_PARAM Username="{_attr(self.user)}" '
                f'Password="{_attr(self.password)}" AuthMethod="1" />')
        try:
            self._send("USER_AUTH_MESSAGE", "CMD_USER_AUTH", body)
            while True:
                mt, xml = self.recv_frame()
                if mt == "USER_AUTH_MESSAGE":
                    break
            root = ET.fromstring(xml.decode("gb2312", "replace"))
            resp = root.find(".//USER_AUTH_RESPONSE")
            if resp is None or not resp.get("Sessionid"):
                raise LoginError("comm_server auth rejected (no session id)")
        except Exception:
            self.close()          # don't leak the socket / camera session on failure
            raise
        self.sessionid = resp.get("Sessionid", "")
        self.group = resp.get("Group")
        return self.sessionid

    # -- PTZ ----------------------------------------------------------------
    def ptz(self, cmd: str, panspeed: int = 3, tiltspeed: int = 3) -> None:
        """Send one PTZ verb. Motion verbs are press-and-hold — follow with
        :meth:`ptz_stop`. Lens verbs: ``zoomtele``/``zoomwide``. Feature verbs:
        ``PtzRestore``/``PtzReboot`` (and preset calls via the web ``<cmd>``)."""
        vcmd = _xml_escape(str(cmd))
        if cmd in const.PTZ_DIRECTIONS or cmd in ("zoomtele", "zoomwide", "stop"):
            body = (f"<xml><cmd>{vcmd}</cmd><panspeed>{int(panspeed)}</panspeed>"
                    f"<tiltspeed>{int(tiltspeed)}</tiltspeed></xml>")
        else:
            body = f"<xml><cmd>{vcmd}</cmd></xml>"
        self._send("PTZ_CONTROL_MESSAGE", "PTZ_CMD", body)

    def ptz_stop(self) -> None:
        self.ptz("stop")

    def heartbeat(self) -> None:
        self._send("AUXPTZ_HEARTBEAT_MESSAGE", "CMD_HEARTBEAT")

    def reboot(self) -> None:
        """Reboot the camera. Captured from AjDevTools "Batch Reboot": a
        ``SYSTEM_CONTROL_MESSAGE``/``1007`` with an empty body. Fire-and-forget —
        the device reboots and drops the connection; reconnect after it comes
        back (~30 s)."""
        self._send("SYSTEM_CONTROL_MESSAGE", "1007")

    # -- file upload (SYSTEM_CONTROL/1022 announce + MEDIA_DATA/1 chunks) ----
    # Captured from AjDevTools "Upload config" against a live MTF45-4G_AF.
    def _media_data_frame(self, start_pos: int, data: bytes) -> bytes:
        env = ('<?xml version="1.0" encoding="GB2312" ?>\n<XML_TOPSEE>\n'
               '<MESSAGE_HEADER Msg_type="MEDIA_DATA_MESSAGE" Msg_code="1" '
               'Msg_flag="0" />\n<MESSAGE_BODY>\n'
               f'<POS StartPos="{start_pos}" DataLen="{len(data)}" />\n'
               '</MESSAGE_BODY>\n</XML_TOPSEE>').encode("gb2312")
        payload = env + b"\x00\x00\x00\x00" + data
        return MAGIC + struct.pack("<I", len(payload)) + payload

    def upload_file(self, content: bytes, remote_path: str = "config.xml", *,
                    file_type: int = 0, chunk_size: int = 60000,
                    confirm: bool = False):
        """Upload *content* to the camera as *remote_path*, via the vendor's
        file-transfer protocol (announce ``SYSTEM_CONTROL_MESSAGE``/``1022`` then
        ``MEDIA_DATA_MESSAGE``/``1`` data chunks ending with a ``DataLen="0"``
        chunk). Verified against a live MTF45-4G_AF ("File upload success").

        Writes to the device, so it is guarded by ``confirm=True``. ``file_type``
        selects how the device treats the file (0 = config; other types route to
        firmware / OEM-default handling — do not guess these on hardware).
        Returns the device's final response frame.
        """
        if not confirm:
            raise AnjoyError("upload_file writes a file to the camera; pass confirm=True")
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if isinstance(content, str):
            content = content.encode("gb2312")
        try:
            str(remote_path).encode("gb2312")          # no lossy '?' in the path
        except UnicodeEncodeError as e:
            raise AnjoyError(f"remote_path is not GB2312-encodable: {remote_path!r}") from e
        n = len(content)
        announce = (f'<REQUEST_PARAM FileType="{int(file_type)}" '
                    f'FilePath="{_attr(remote_path)}" FileLength="{n}" />')
        self._send("SYSTEM_CONTROL_MESSAGE", "1022", announce)
        self.sock.settimeout(self.timeout)
        try:
            self._recv_until("SYSTEM_CONTROL_MESSAGE", "1022")   # device ready
            pos = 0
            while pos < n:
                piece = content[pos:pos + chunk_size]
                self.sock.sendall(self._media_data_frame(pos, piece))
                pos += len(piece)
            self.sock.sendall(self._media_data_frame(n, b""))    # EOF
            return self._recv_until("SYSTEM_CONTROL_MESSAGE", "1001")  # success
        except socket.timeout as e:
            raise AnjoyError("timed out during file upload; outcome uncertain") from e

    def _recv_until(self, msg_type: str, msg_code: str | None = None,
                    limit: int = 32):
        for _ in range(limit):
            mt, xml = self.recv_frame()
            if mt != msg_type:
                continue
            if msg_code is None:
                return mt, xml
            m = re.search(rb'Msg_code="([^"]+)"', xml)
            if m and m.group(1).decode("ascii", "replace") == msg_code:
                return mt, xml
        want = msg_type + (f"/{msg_code}" if msg_code else "")
        raise AnjoyError(f"no {want} received")

    # -- EXECUTE_USER_CMD (remote shell) — CONFIRMED executing on hardware -----
    def exec_cmd(self, *commands: str, remote_name: str = "defaultconfig.xml",
                 confirm: bool = False):
        """Run shell *commands* on the camera via ``EXECUTE_USER_CMD``.

        ⚠️ Runs arbitrary shell, so guarded by ``confirm=True``.

        Mechanism (captured + confirmed on a live MTF45-4G_AF): the payload
        ``<EXECUTE_USER_CMD><CMD DATA="cmd"/>…</EXECUTE_USER_CMD>`` (each command
        XML-escaped and GB2312-validated) is delivered via :meth:`upload_file`
        under an **OEM-default config basename** — ``comm_server`` copies it into
        ``/mnt/nand/cust/`` and ``mainctrl``'s ``get_user_cmd_from_xml`` runs each
        command. Verified live: a ``killall comm_server`` this way actually killed
        the process. Recognised names: ``defaultconfig.xml``, ``config.default.xml``,
        ``default_2_priority.xml`` (an arbitrary basename is only *stored*).

        Side effects to know:
        * The uploaded file **persists** as the OEM-default config in
          ``/mnt/nand/cust/`` and its commands re-run on factory reset — this is
          the vendor's own ``ptzClear.xml`` behaviour. Make commands self-cleaning
          (e.g. end with ``rm -f /mnt/nand/cust/<remote_name>``) if you don't want
          that.
        * A command that stops ``comm_server`` (e.g. ``killall comm_server``) drops
          this connection as it runs; :class:`AnjoyError` ("comm_server closed") is
          then expected and the daemon is respawned by ``procman``.
        """
        if not confirm:
            raise AnjoyError("exec_cmd runs arbitrary shell on the camera; pass confirm=True")
        if remote_name not in EXEC_TRIGGER_NAMES:
            raise ValueError(
                f"remote_name {remote_name!r} is not an executing OEM-default name; "
                f"commands would be stored, not run. Use one of {EXEC_TRIGGER_NAMES} "
                "(or call upload_file directly to just store a file).")
        content = _exec_body(commands).encode("gb2312")
        return self.upload_file(content, remote_name, file_type=0, confirm=True)

    # -- file download (SYSTEM_CONTROL/1023 + MEDIA_DATA/2 chunks) -----------
    def download_file(self, remote_path: str) -> bytes:
        """Download a file from the camera. Captured from AjDevTools "Batch
        Download Config" and verified on a live MTF45-4G_AF.

        Announce ``SYSTEM_CONTROL_MESSAGE``/``1023`` with
        ``<REQUEST_PARAM FileName="…" StartPos="0"/>``; the device replies
        ``<RESPONSE_PARAM Port="8091" Type="1" FileLength="N"/>`` then streams
        ``MEDIA_DATA_MESSAGE``/``2`` chunks (``<POS … DataLen="L"/>`` + a 4-byte
        separator + L bytes) ending with ``DataLen="0"``. Read-only.
        """
        try:
            str(remote_path).encode("gb2312")            # no lossy '?' in the path
        except UnicodeEncodeError as e:
            raise AnjoyError(f"remote_path is not GB2312-encodable: {remote_path!r}") from e
        ann = f'<REQUEST_PARAM FileName="{_attr(remote_path)}" StartPos="0" />'
        self._send("SYSTEM_CONTROL_MESSAGE", "1023", ann)
        self.sock.settimeout(self.timeout)
        _, resp = self._recv_until("SYSTEM_CONTROL_MESSAGE", "1023")
        m = re.search(rb'FileLength="(\d+)"', resp)
        total = int(m.group(1)) if m else None
        out = bytearray()
        while True:                                       # read until the DataLen=0 EOF
            mt, payload = self.recv_frame(strip_null=False)
            if mt != "MEDIA_DATA_MESSAGE":
                continue
            dm = re.search(rb'DataLen="(\d+)"', payload)
            dlen = int(dm.group(1)) if dm else 0
            if dlen == 0:
                break
            out += payload[-dlen:]                        # exact raw trailing bytes
        if total is not None and len(out) != total:
            raise AnjoyError(f"incomplete download: got {len(out)} of {total} bytes")
        return bytes(out)

    def get_config(self, remote_path: str = const.CONFIG_PATH) -> bytes:
        """Download the device's full ``<IPCConfig>`` config XML (the config
        backup). Convenience wrapper over :meth:`download_file`."""
        return self.download_file(remote_path)

    # -- config section set (SYSTEM_CONFIG_SET_MESSAGE) ----------------------
    def set_config_section(self, code, body: str, *, confirm: bool = False):
        """Write one device config section via ``SYSTEM_CONFIG_SET_MESSAGE``.

        Captured from AjDevTools' per-feature batch buttons (e.g. "Batch Set
        Title") against a live MTF45-4G_AF: the tool sends
        ``SYSTEM_CONFIG_SET_MESSAGE`` with the section's numeric ``Msg_code`` and
        the section element as *body*, and the device replies with the **same
        type+code and an empty body** to acknowledge. A *partial* section is
        accepted — the device merges it into the stored config, so you may send
        only the attributes you want to change.

        The write is applied **asynchronously**: the ack returns at once but the
        change reaches ``/mnt/nand/config.xml`` (and :meth:`get_config`) a moment
        later, so pause briefly before reading it back to confirm.

        Reads use the full-config download (:meth:`get_config`) — the device's
        per-section GET is not what the vendor tool uses. Writes to the device,
        so guarded by ``confirm=True``. *code* is a section code (see
        ``anjoy.const`` ``CFG_*``, e.g. :data:`~anjoy.const.CFG_OVERLAY`);
        *body* is the section XML. Returns the device's ack frame.
        """
        if not confirm:
            raise AnjoyError("set_config_section writes device config; pass confirm=True")
        code = str(code)
        try:
            str(body).encode("gb2312")          # the wire encoding — no lossy '?'
        except UnicodeEncodeError as e:
            raise AnjoyError(f"config body is not GB2312-encodable: {body!r}") from e
        self._send("SYSTEM_CONFIG_SET_MESSAGE", code, body)
        self.sock.settimeout(self.timeout)
        mt, xml = self._recv_until("SYSTEM_CONFIG_SET_MESSAGE", code)
        # Success is the same type+code with an EMPTY body; a matching frame that
        # carries a payload is an error/status, not an ack — don't report success.
        payload = _message_body(xml)
        if payload:
            raise AnjoyError(
                f"config set (code {code}) not acknowledged: "
                f"{payload[:200].decode('gb2312', 'replace')}")
        return mt, xml

    # -- typed config setters (thin wrappers over set_config_section) --------
    def set_title(self, title: str, *, confirm: bool = False):
        """Set the OSD title text (``MediaConfig/Video/Overlay``, code 525).

        Read-modify-write: the current ``<Overlay>`` section is downloaded and
        only ``<TitleOverlay>``'s title is changed, so the rest of the OSD
        (position, font, timestamp, any extra user-OSD lines) is preserved. The
        title is stored hex-encoded — ``TitleUtf8`` as the hex of the UTF-8 bytes
        and the legacy ``Title`` (when present) as the hex of the GB2312 bytes.
        Verified live on MTF45-4G_AF. Writes — ``confirm=True``.
        """
        if not confirm:
            raise AnjoyError("set_title writes device config; pass confirm=True")
        cfg = self.get_config()
        m = re.search(rb"<Overlay\b.*?</Overlay>", cfg, re.S)
        if not m:
            raise AnjoyError("device config has no <Overlay> section")
        root = ET.fromstring(m.group(0).decode("gb2312", "replace"))
        t = root.find("TitleOverlay")
        if t is None:
            raise AnjoyError("device Overlay config has no <TitleOverlay>")
        t.set("TitleUtf8", title.encode("utf-8").hex())   # always safe: hex of UTF-8
        if t.get("Title") is not None:
            # the legacy Title field carries GB2312 bytes — only this path is
            # charset-limited, so validate here rather than rejecting every title.
            try:
                t.set("Title", title.encode("gb2312").hex())
            except UnicodeEncodeError as e:
                raise AnjoyError(
                    "title needs the legacy GB2312 <TitleOverlay Title> field but "
                    f"is not GB2312-encodable: {title!r}") from e
        body = ET.tostring(root, encoding="unicode")
        return self.set_config_section(const.CFG_OVERLAY, body, confirm=True)

    def set_maintenance(self, enable: bool, *, day: int = 7,
                        time: str = "00:00:00", confirm: bool = False):
        """Set the scheduled auto-reboot (``SystemConfig/MaintainConfig``, code
        228). *enable* turns it on/off; *day* is the vendor day selector
        (``7`` = every day) and *time* is ``HH:MM:SS``. Captured from AjDevTools
        "Batch Timing Maintenance" and verified live. Writes — ``confirm=True``.
        """
        if not confirm:
            raise AnjoyError("set_maintenance writes device config; pass confirm=True")
        body = (f'<MaintainConfig Enable="{1 if enable else 0}" '
                f'Day="{int(day)}" Time="{_attr(time)}" />')
        return self.set_config_section(const.CFG_MAINTAIN, body, confirm=True)

    def snapshot(self, stream: int = 0, quality: int = 100) -> bytes:
        """Capture a JPEG snapshot and return its bytes. Captured from AjDevTools
        "Batch Snap Picture" and verified live.

        Trigger ``SYSTEM_CONTROL_MESSAGE``/``1043`` with
        ``<REQUEST_PARAM Stream="S" Quality="Q"/>``; the device saves a JPEG under
        ``/tmp`` and replies ``<RESPONSE_PARAM>JpgFile="…"</RESPONSE_PARAM>``, which
        is then fetched with :meth:`download_file`. *stream* 0=main, 1=sub;
        *quality* 1-100. Read-only.
        """
        if int(stream) not in (0, 1):
            raise ValueError("stream must be 0 (main) or 1 (sub)")
        if not 1 <= int(quality) <= 100:
            raise ValueError("quality must be 1-100")
        body = f'<REQUEST_PARAM Stream="{int(stream)}" Quality="{int(quality)}"/>'
        self._send("SYSTEM_CONTROL_MESSAGE", "1043", body)
        self.sock.settimeout(self.timeout)
        _, resp = self._recv_until("SYSTEM_CONTROL_MESSAGE", "1043")
        m = re.search(rb'JpgFile="([^"]+)"', resp)
        if not m:
            raise AnjoyError("snapshot: device returned no JpgFile")
        name = m.group(1).decode("gb2312", "replace")   # protocol encoding, not ASCII
        return self.download_file("/tmp/" + name)

    def build_exec_frame(self, *commands: str) -> bytes:
        """Build (without sending) the framed ``EXECUTE_USER_CMD`` **file bytes**
        wrapped in a single MEDIA_DATA data frame — for tests and inspection."""
        content = _exec_body(commands).encode("gb2312")
        return self._media_data_frame(0, content)

    def events(self, heartbeat_on_idle: bool = True) -> Iterator[tuple[str, bytes]]:
        """Yield frames the camera pushes (e.g. ALARM_REPORT_MESSAGE) indefinitely.

        Alarms are sparse, so a read timeout is *not* the end of the stream: it is
        swallowed (optionally sending a heartbeat to keep the session alive) and
        the loop continues. A closed connection or a real protocol error still
        propagates.
        """
        while True:
            try:
                yield self.recv_frame()
            except socket.timeout:
                if heartbeat_on_idle:
                    try:
                        self.heartbeat()
                    except OSError:
                        raise
                continue
