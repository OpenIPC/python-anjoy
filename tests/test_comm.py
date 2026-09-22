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
    def close(self):
        pass
    def settimeout(self, t):
        pass


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


class TestReviewFixes(unittest.TestCase):
    def test_credentials_are_xml_escaped(self):
        c = AnjoyCommClient("x", user='a"b&c', password='p<>"&')
        c.sock = _FakeSock()
        # build the auth frame the way login() does
        from anjoy.comm import _attr
        body = (f'<USER_AUTH_PARAM Username="{_attr(c.user)}" '
                f'Password="{_attr(c.password)}" AuthMethod="1" />')
        c._send("USER_AUTH_MESSAGE", "CMD_USER_AUTH", body)
        sent = c.sock.sent
        self.assertNotIn(b'Username="a"b&c"', sent)        # raw quote/amp gone
        self.assertIn(b"&quot;", sent)
        self.assertIn(b"&amp;", sent)
        # the frame body still parses as XML
        import xml.etree.ElementTree as ET
        ET.fromstring(sent[8:].decode("gb2312").lstrip())

    def test_ptz_cmd_escaped(self):
        c = AnjoyCommClient("x"); c.sock = _FakeSock()
        c.ptz('a<b&c"')
        import xml.etree.ElementTree as ET
        ET.fromstring(c.sock.sent[8:].decode("gb2312").lstrip())  # well-formed

    def test_recv_frame_rejects_oversized_length(self):
        from anjoy.comm import MAGIC, MAX_FRAME
        from anjoy.exceptions import AnjoyError
        big = struct.pack("<I", MAX_FRAME + 1)
        c = AnjoyCommClient("x"); c.sock = _FakeSock(MAGIC + big)
        with self.assertRaises(AnjoyError):
            c.recv_frame()

    def test_connect_close_reset_state(self):
        c = AnjoyCommClient("x")
        c.sessionid = "S"; c.group = "G"; c._buf = b"leftover"
        c.close()
        self.assertEqual((c.sessionid, c.group, c._buf), ("", None, b""))

    def test_login_closes_socket_on_failure(self):
        # a fake sock that returns a non-auth frame then EOF -> login should fail + close
        from anjoy.comm import MAGIC
        body = build_envelope("SYSTEM_CONTROL_MESSAGE", "1020")
        frame = MAGIC + struct.pack("<I", len(body)) + body
        c = AnjoyCommClient("x")
        c.sock = _FakeSock(frame)   # no USER_AUTH response, then recv returns b"" -> error
        from anjoy.exceptions import AnjoyError
        with self.assertRaises(AnjoyError):
            c.login()
        self.assertIsNone(c.sock)   # closed

    def test_events_survives_idle_timeout(self):
        import socket as _s
        from anjoy.comm import MAGIC
        alarm = build_envelope("ALARM_REPORT_MESSAGE", "CMD_REPORT_ALARM")
        frame = MAGIC + struct.pack("<I", len(alarm)) + alarm

        class TimeoutOnceSock:
            def __init__(self): self.calls = 0; self._data = frame; self.sent = b""
            def recv(self, n):
                self.calls += 1
                if self.calls == 1:
                    raise _s.timeout("idle")
                out, self._data = self._data[:n], self._data[n:]
                return out
            def sendall(self, b): self.sent += b
            def close(self): pass

        c = AnjoyCommClient("x"); c.sock = TimeoutOnceSock()
        gen = c.events(heartbeat_on_idle=True)
        mt, _ = next(gen)                       # first recv times out -> heartbeat -> retries
        self.assertEqual(mt, "ALARM_REPORT_MESSAGE")
        self.assertIn(b"AUXPTZ_HEARTBEAT_MESSAGE", c.sock.sent)   # heartbeat was sent


    def test_events_resumes_after_timeout_mid_body(self):
        # A timeout AFTER the header but mid-body must not reparse buffered body
        # bytes as a new header (regression for the resumable-frame fix).
        import socket as _s
        from anjoy.comm import MAGIC
        alarm = build_envelope("ALARM_REPORT_MESSAGE", "CMD_REPORT_ALARM")
        frame = MAGIC + struct.pack("<I", len(alarm)) + alarm
        split = 8 + 5   # header + 5 body bytes, then a timeout, then the rest

        class SplitSock:
            def __init__(self): self.stage = 0; self.sent = b""
            def recv(self, n):
                self.stage += 1
                if self.stage == 1:
                    return frame[:split]        # header + partial body
                if self.stage == 2:
                    raise _s.timeout("idle mid-body")
                return frame[split:]            # remainder of the body
            def sendall(self, b): self.sent += b
            def close(self): pass

        c = AnjoyCommClient("x"); c.sock = SplitSock()
        mt, xml = next(c.events(heartbeat_on_idle=False))
        self.assertEqual(mt, "ALARM_REPORT_MESSAGE")


class TestExecUserCmd(unittest.TestCase):
    def test_requires_confirm(self):
        from anjoy.exceptions import AnjoyError
        c = AnjoyCommClient("x"); c.sock = _FakeSock()
        with self.assertRaises(AnjoyError):
            c.exec_cmd("echo hi")            # no confirm=True

    def test_build_exec_frame_is_well_formed_and_escaped(self):
        import xml.etree.ElementTree as ET
        c = AnjoyCommClient("x")
        frame = c.build_exec_frame('echo "a&b"', "killall comm_server")
        self.assertEqual(frame[:4], MAGIC)
        root = ET.fromstring(frame[8:].decode("gb2312").lstrip())
        cmds = [e.get("DATA") for e in root.iter("CMD")]
        self.assertEqual(cmds, ['echo "a&b"', "killall comm_server"])
        self.assertIn(b'Msg_type="SYSTEM_CONFIG_SET_MESSAGE"', frame)

    def test_rejects_non_gb2312_command(self):
        from anjoy.exceptions import AnjoyError
        c = AnjoyCommClient("x")
        with self.assertRaises(AnjoyError):
            c.build_exec_frame("echo \U0001F3A5")   # emoji: not GB2312-encodable

    def test_exec_skips_pushed_alarm_then_returns_ack(self):
        c = AnjoyCommClient("x")
        alarm = build_envelope("ALARM_REPORT_MESSAGE", "CMD_REPORT_ALARM")
        ack = build_envelope("SYSTEM_CONFIG_SET_MESSAGE", "CMD_CONFIG_UPDATE")
        inbound = (MAGIC + struct.pack("<I", len(alarm)) + alarm +
                   MAGIC + struct.pack("<I", len(ack)) + ack)
        c.sock = _FakeSock(inbound)
        mt, _ = c.exec_cmd("true", confirm=True)     # alarm skipped, ack returned
        self.assertEqual(mt, "SYSTEM_CONFIG_SET_MESSAGE")

    def test_exec_raises_on_timeout(self):
        import socket as _s
        from anjoy.exceptions import AnjoyError
        class TimeoutSock:
            def __init__(self): self.sent = b""
            def sendall(self, b): self.sent += b
            def recv(self, n): raise _s.timeout("no ack")
            def settimeout(self, t): pass
            def close(self): pass
        c = AnjoyCommClient("x"); c.sock = TimeoutSock()
        with self.assertRaises(AnjoyError):
            c.exec_cmd("true", confirm=True)

    def test_exec_cmd_roundtrip_against_fake_server(self):
        with FakeCommServer() as srv:
            c = AnjoyCommClient("127.0.0.1", "admin", "123456", port=srv.port)
            c.connect(); c.login()
            mt, _ = c.exec_cmd("true", confirm=True)
            self.assertEqual(mt, "SYSTEM_CONFIG_SET_MESSAGE")   # device ack
            c.close()
        self.assertIn("SYSTEM_CONFIG_SET_MESSAGE", [t for t, _ in srv.received])


if __name__ == "__main__":
    unittest.main()
