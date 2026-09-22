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

    def recv_frame(self) -> tuple[str, bytes]:
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
        if isinstance(content, str):
            content = content.encode("gb2312")
        n = len(content)
        announce = (f'<REQUEST_PARAM FileType="{int(file_type)}" '
                    f'FilePath="{_attr(remote_path)}" FileLength="{n}" />')
        self._send("SYSTEM_CONTROL_MESSAGE", "1022", announce)
        self.sock.settimeout(self.timeout)
        self._recv_until("SYSTEM_CONTROL_MESSAGE")     # device: RESPONSE_PARAM ready
        pos = 0
        while pos < n:
            piece = content[pos:pos + chunk_size]
            self.sock.sendall(self._media_data_frame(pos, piece))
            pos += len(piece)
        self.sock.sendall(self._media_data_frame(n, b""))   # EOF
        try:
            return self._recv_until("SYSTEM_CONTROL_MESSAGE")
        except socket.timeout:
            return ("", b"")

    def _recv_until(self, msg_type: str, limit: int = 32):
        for _ in range(limit):
            mt, xml = self.recv_frame()
            if mt == msg_type:
                return mt, xml
        raise AnjoyError(f"no {msg_type} received")

    # -- EXECUTE_USER_CMD (remote shell) — delivered as a config file ---------
    def exec_cmd(self, *commands: str, remote_path: str = "ptzClear.xml",
                 file_type: int = 0, confirm: bool = False):
        """Deliver an ``EXECUTE_USER_CMD`` payload to the camera as a config file.

        ⚠️ This asks the camera to run arbitrary shell, so it is guarded by
        ``confirm=True``. Builds ``<EXECUTE_USER_CMD><CMD DATA="cmd"/>…>`` (each
        command XML-escaped and GB2312-validated) and sends it via
        :meth:`upload_file` — the vendor's real delivery mechanism (captured from
        AjDevTools).

        Verification (honest): the file-upload transport is confirmed (the device
        returns "File upload success"), but with ``file_type=0`` (config) the
        embedded command is *stored, not executed* — a live ``killall comm_server``
        via this path did not restart the process. The device only runs
        ``EXECUTE_USER_CMD`` when the file is processed as an OEM-default config
        (``SET_OEM_DEFAULT_CONFIG`` in ``mainctrl``); the ``file_type``/target that
        triggers that was not pinned down (and must not be brute-forced on
        hardware — that path neighbours ``CLEARALL`` and ``rm /mnt/nand/*``).
        """
        if not confirm:
            raise AnjoyError(
                "exec_cmd runs arbitrary shell on the camera; pass confirm=True. "
                "Note: execution is UNVERIFIED (config file_type stores, not runs).")
        content = _exec_body(commands).encode("gb2312")
        return self.upload_file(content, remote_path, file_type=file_type, confirm=True)

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
