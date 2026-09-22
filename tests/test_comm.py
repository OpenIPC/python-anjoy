"""Binary AJ protocol (comm_server 8091): framing, auth, PTZ — deviceless.

The wire format is validated against bytes captured from the vendor tool and,
in CI, against FakeCommServer which reproduces the device's framing including
its NULL-terminated responses.
"""

import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from anjoy import comm
from anjoy.comm import AnjoyCommClient, build_envelope, build_frame, MAGIC
from tests.fake_server import FakeCommServer


class TestFraming(unittest.TestCase):
    def test_magic_and_length(self):
        frame = build_frame(b"hello")
        self.assertEqual(frame[:4], MAGIC)
        self.assertEqual(struct.unpack("<I", frame[4:8])[0], 5)
        self.assertEqual(frame[8:], b"hello")

    def test_auth_envelope_matches_captured(self):
        # Byte-for-byte the frame the vendor tool sent (verified to elicit a
        # USER_AUTH_RESPONSE from a real MTF45-4G_AF).
        env = build_envelope(
            "USER_AUTH_MESSAGE", "CMD_USER_AUTH",
            '<USER_AUTH_PARAM Username="admin" Password="123456" AuthMethod="1" />')
        expected = (
            '\n<?xml version="1.0" encoding="GB2312" ?>\n<XML_TOPSEE>\n'
            '<MESSAGE_HEADER Msg_type="USER_AUTH_MESSAGE" Msg_code="CMD_USER_AUTH" '
            'Msg_channel="0" Msg_flag="0" Sessionid=""  />\n<MESSAGE_BODY>\n'
            '<USER_AUTH_PARAM Username="admin" Password="123456" AuthMethod="1" />\n'
            '</MESSAGE_BODY>\n</XML_TOPSEE>\n').encode("gb2312")
        self.assertEqual(env, expected)

    def test_empty_body_self_closes(self):
        env = build_envelope("AUXPTZ_HEARTBEAT_MESSAGE", "CMD_HEARTBEAT").decode("gb2312")
        self.assertIn("<MESSAGE_BODY/>", env)


class _FakeSock:
    """Feeds a fixed byte stream to recv(); records sendall()."""
    def __init__(self, inbound=b""):
        self._in = inbound
        self.sent = b""
    def recv(self, n):
        out, self._in = self._in[:n], self._in[n:]
        return out
    def sendall(self, b):
        self.sent += b


class TestRecvFrame(unittest.TestCase):
    def _client_with(self, inbound):
        c = AnjoyCommClient("x")
        c.sock = _FakeSock(inbound)
        return c

    def test_parses_null_terminated_frame(self):
        # Device NULL-terminates; recv_frame must strip it and still parse.
        body = build_envelope("USER_AUTH_MESSAGE", "CMD_USER_AUTH",
                              '<USER_AUTH_RESPONSE Sessionid="S1" Group="Administrator"/>')
        frame = MAGIC + struct.pack("<I", len(body) + 1) + body + b"\x00"
        c = self._client_with(frame)
        mt, xml = c.recv_frame()
        self.assertEqual(mt, "USER_AUTH_MESSAGE")
        self.assertNotIn(b"\x00", xml)

    def test_bad_magic_raises(self):
        from anjoy.exceptions import AnjoyError
        c = self._client_with(b"XXXX\x04\x00\x00\x00abcd")
        with self.assertRaises(AnjoyError):
            c.recv_frame()


class TestPtzBodies(unittest.TestCase):
    def test_ptz_move_body(self):
        c = AnjoyCommClient("x")
        c.sock = _FakeSock()
        c.ptz("zoomtele", panspeed=3, tiltspeed=3)
        self.assertIn(b"<cmd>zoomtele</cmd><panspeed>3</panspeed><tiltspeed>3</tiltspeed>",
                      c.sock.sent)
        self.assertIn(b'Msg_type="PTZ_CONTROL_MESSAGE"', c.sock.sent)

    def test_ptz_feature_verb_no_speed(self):
        c = AnjoyCommClient("x")
        c.sock = _FakeSock()
        c.ptz("PtzReboot")
        self.assertIn(b"<xml><cmd>PtzReboot</cmd></xml>", c.sock.sent)


class TestAgainstFakeServer(unittest.TestCase):
    def test_login_and_ptz(self):
        with FakeCommServer(sessionid="20260101000000_deadbeefdeadbeef") as srv:
            c = AnjoyCommClient("127.0.0.1", "admin", "123456", port=srv.port)
            c.connect()
            sid = c.login()
            self.assertEqual(sid, "20260101000000_deadbeefdeadbeef")
            self.assertEqual(c.group, "Administrator")
            c.ptz("zoomtele")
            mt, _ = c.recv_frame()          # server echoes the PTZ frame
            self.assertEqual(mt, "PTZ_CONTROL_MESSAGE")
            c.close()
        # server saw the auth then the ptz
        types = [t for t, _ in srv.received]
        self.assertEqual(types[0], "USER_AUTH_MESSAGE")
        self.assertIn("PTZ_CONTROL_MESSAGE", types)


if __name__ == "__main__":
    unittest.main()
