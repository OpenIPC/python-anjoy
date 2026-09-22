"""A scriptable in-process HTTP server that mimics the Anjoy web SOAP API.

Speaks the same POST-a-SOAP-envelope contract as a real device: it parses the
``<userid>``/``<passwd>`` DES-hex auth header, records every (endpoint, body)
seen, and dispatches to a per-test ``handlers`` map. Unknown endpoints return a
JSON error envelope. No device or network egress needed.
"""

from __future__ import annotations

import threading
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, HTTPServer

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from anjoy.des import des_hex  # noqa: E402


class FakeAnjoyHTTPServer:
    """Threaded loopback HTTP server. Use as a context manager.

    handlers: ``{endpoint_path: callable(body_xml) -> response_text}``.
    Auth is checked against ``des_hex(user)`` / ``des_hex(password)``.
    """

    def __init__(self, handlers=None, user="admin", password="123456"):
        self.handlers = handlers or {}
        self.user = user
        self.password = password
        self.received = []          # list of (endpoint, body_xml)
        self.auth_seen = []         # list of (userid_hex, passwd_hex)

        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length).decode("utf-8", "replace")
                userid, passwd, body = outer._parse_envelope(raw)
                outer.auth_seen.append((userid, passwd))
                outer.received.append((self.path, body))
                handler = outer.handlers.get(self.path)
                if handler is None:
                    resp = '{"result":false,"error":{"code":404,"message":"no such endpoint"}}'
                    status = 404
                else:
                    resp = handler(body)
                    status = 200
                data = resp.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/xml")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._httpd.server_address[1]
        self._thread = None

    @staticmethod
    def _parse_envelope(raw: str):
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            return None, None, raw
        ns = {"soap": "http://www.w3.org/2001/12/soap-envelope"}
        uid = root.findtext(".//soap:Header/userid", namespaces=ns)
        pwd = root.findtext(".//soap:Header/passwd", namespaces=ns)
        body_el = root.find(".//soap:Body", ns)
        body = ""
        if body_el is not None and len(body_el):
            body = ET.tostring(body_el[0], encoding="unicode")
        return uid, pwd, body

    @property
    def expected_auth(self):
        return des_hex(self.user), des_hex(self.password)

    def __enter__(self):
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._httpd.shutdown()
        self._httpd.server_close()


class FakeCommServer:
    """Threaded loopback server mimicking Anjoy's comm_server (TCP 8091).

    Speaks the 58 91 58 51 + LE-length + GB2312-XML framing, answers USER_AUTH
    with a session id, echoes PTZ frames, and — like the real device —
    NULL-terminates its response frames.
    """

    MAGIC = b"\x58\x91\x58\x51"

    def __init__(self, sessionid="20260101000000_deadbeefdeadbeef"):
        import socket
        self.sessionid = sessionid
        self.received = []           # list of (msg_type, body_xml_bytes)
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self._thread = None

    @staticmethod
    def _frame(xml_bytes, null_term=True):
        import struct
        body = xml_bytes + (b"\x00" if null_term else b"")
        return FakeCommServer.MAGIC + struct.pack("<I", len(body)) + body

    def _read_frame(self, conn):
        import struct
        hdr = b""
        while len(hdr) < 8:
            c = conn.recv(8 - len(hdr))
            if not c:
                raise EOFError
            hdr += c
        assert hdr[:4] == self.MAGIC
        ln = struct.unpack("<I", hdr[4:8])[0]
        body = b""
        while len(body) < ln:
            c = conn.recv(ln - len(body))
            if not c:
                raise EOFError
            body += c
        return body

    def __enter__(self):
        import threading
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        try:
            self.srv.close()
        except Exception:
            pass

    def _serve(self):
        import re
        try:
            conn, _ = self.srv.accept()
        except OSError:
            return
        with conn:
            while True:
                try:
                    body = self._read_frame(conn)
                except (EOFError, OSError):
                    break
                mt = ""
                m = re.search(rb'Msg_type="([^"]+)"', body)
                if m:
                    mt = m.group(1).decode()
                self.received.append((mt, body))
                if mt == "USER_AUTH_MESSAGE":
                    resp = (
                        '<?xml version="1.0" encoding="GB2312" ?>\n<XML_TOPSEE>\n'
                        '<MESSAGE_HEADER\nMsg_type="USER_AUTH_MESSAGE"\n'
                        'Msg_code="CMD_USER_AUTH"\nMsg_flag="0"\n/>\n<MESSAGE_BODY>\n'
                        f'<USER_AUTH_RESPONSE\nSessionid="{self.sessionid}"\n'
                        'Group="Administrator" myversion="1" \n/>\n'
                        '</MESSAGE_BODY>\n</XML_TOPSEE>').encode("gb2312")
                    conn.sendall(self._frame(resp))          # null-terminated
                elif mt == "PTZ_CONTROL_MESSAGE":
                    conn.sendall(self._frame(body))          # echo the frame back
                elif mt == "SYSTEM_CONFIG_SET_MESSAGE":
                    ack = ('<?xml version="1.0" encoding="GB2312" ?>\n<XML_TOPSEE>\n'
                           '<MESSAGE_HEADER\nMsg_type="SYSTEM_CONFIG_SET_MESSAGE"\n'
                           'Msg_code="CMD_CONFIG_UPDATE"\nMsg_flag="0"\n/>\n'
                           '<MESSAGE_BODY>\n</MESSAGE_BODY>\n</XML_TOPSEE>').encode("gb2312")
                    conn.sendall(self._frame(ack))
