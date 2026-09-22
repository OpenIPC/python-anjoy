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
    def test_exec_requires_confirm(self):
        from anjoy.exceptions import AnjoyError
        c = AnjoyCommClient("x"); c.sock = _FakeSock()
        with self.assertRaises(AnjoyError):
            c.exec_cmd("echo hi")

    def test_upload_requires_confirm(self):
        from anjoy.exceptions import AnjoyError
        c = AnjoyCommClient("x"); c.sock = _FakeSock()
        with self.assertRaises(AnjoyError):
            c.upload_file(b"data")

    def test_build_exec_frame_content_wellformed_and_escaped(self):
        import xml.etree.ElementTree as ET
        c = AnjoyCommClient("x")
        frame = c.build_exec_frame('echo "a&b"', "killall comm_server")
        self.assertEqual(frame[:4], MAGIC)
        self.assertIn(b"MEDIA_DATA_MESSAGE", frame)          # delivered as file data
        i = frame.index(b"<EXECUTE_USER_CMD>")
        root = ET.fromstring(frame[i:].decode("gb2312"))     # the file content parses
        self.assertEqual([e.get("DATA") for e in root.iter("CMD")],
                         ['echo "a&b"', "killall comm_server"])

    def test_rejects_non_gb2312_command(self):
        from anjoy.exceptions import AnjoyError
        c = AnjoyCommClient("x")
        with self.assertRaises(AnjoyError):
            c.build_exec_frame("echo \U0001F3A5")           # emoji: not GB2312

    def test_upload_file_roundtrip_reassembles_content(self):
        with FakeCommServer() as srv:
            c = AnjoyCommClient("127.0.0.1", "admin", "123456", port=srv.port)
            c.connect(); c.login()
            c.upload_file(b"HELLO-ANJOY-UPLOAD", "config.xml", confirm=True)
            c.close()
        self.assertEqual(srv.upload_data, b"HELLO-ANJOY-UPLOAD")

    def test_exec_cmd_delivers_execute_user_cmd_payload(self):
        with FakeCommServer() as srv:
            c = AnjoyCommClient("127.0.0.1", "admin", "123456", port=srv.port)
            c.connect(); c.login()
            c.exec_cmd("true", confirm=True)
            c.close()
        self.assertIn(b"<EXECUTE_USER_CMD>", srv.upload_data)
        self.assertIn(b'DATA="true"', srv.upload_data)
        # the announce named the file-upload control message
        self.assertIn("SYSTEM_CONTROL_MESSAGE", [t for t, _ in srv.received])

    def test_upload_rejects_nonpositive_chunk(self):
        c = AnjoyCommClient("x"); c.sock = _FakeSock()
        with self.assertRaises(ValueError):
            c.upload_file(b"data", chunk_size=0, confirm=True)

    def test_upload_rejects_non_gb2312_path(self):
        from anjoy.exceptions import AnjoyError
        c = AnjoyCommClient("x"); c.sock = _FakeSock()
        with self.assertRaises(AnjoyError):
            c.upload_file(b"data", "\U0001F4C1.xml", confirm=True)

    def test_upload_raises_on_timeout(self):
        import socket as _s
        from anjoy.exceptions import AnjoyError
        class TimeoutSock:
            def __init__(self): self.sent = b""
            def sendall(self, b): self.sent += b
            def recv(self, n): raise _s.timeout("no ready ack")
            def settimeout(self, t): pass
            def close(self): pass
        c = AnjoyCommClient("x"); c.sock = TimeoutSock(); c.sessionid = "S"
        with self.assertRaises(AnjoyError):
            c.upload_file(b"data", confirm=True)

    def test_exec_rejects_non_executing_name(self):
        c = AnjoyCommClient("x"); c.sock = _FakeSock()
        with self.assertRaises(ValueError):
            c.exec_cmd("true", remote_name="whatever.xml", confirm=True)


class TestDownload(unittest.TestCase):
    def test_download_file_reassembles(self):
        with FakeCommServer() as srv:
            srv.download_content = b"<IPCConfig>hello-config-payload</IPCConfig>"
            c = AnjoyCommClient("127.0.0.1", "admin", "123456", port=srv.port)
            c.connect(); c.login()
            data = c.download_file("/mnt/nand/config.xml")
            c.close()
        self.assertEqual(data, b"<IPCConfig>hello-config-payload</IPCConfig>")

    def test_get_config_uses_config_path(self):
        from anjoy import const
        with FakeCommServer() as srv:
            srv.download_content = b"<IPCConfig/>"
            c = AnjoyCommClient("127.0.0.1", "admin", "123456", port=srv.port)
            c.connect(); c.login()
            self.assertEqual(c.get_config(), b"<IPCConfig/>")
            c.close()
        # the request named the on-device config path
        self.assertTrue(any(const.CONFIG_PATH.encode() in b for _, b in srv.received))


    def test_download_preserves_trailing_bytes(self):
        with FakeCommServer() as srv:
            srv.download_content = b"\x89PNGbinary\x00\x00\x00"
            c = AnjoyCommClient("127.0.0.1", "admin", "123456", port=srv.port)
            c.connect(); c.login()
            data = c.download_file("/mnt/nand/x.bin")
            c.close()
        self.assertEqual(data, b"\x89PNGbinary\x00\x00\x00")

    def test_download_rejects_non_gb2312_path(self):
        from anjoy.exceptions import AnjoyError
        c = AnjoyCommClient("x"); c.sock = _FakeSock()
        with self.assertRaises(AnjoyError):
            c.download_file("/mnt/\U0001F4C1.xml")

    def test_download_incomplete_raises(self):
        from anjoy.exceptions import AnjoyError
        from anjoy.comm import MAGIC
        resp = build_envelope("SYSTEM_CONTROL_MESSAGE", "1023",
                              '<RESPONSE_PARAM Port="8091" Type="1" FileLength="100" />')
        env = ('<?xml version="1.0" encoding="GB2312" ?>\n<XML_TOPSEE>\n'
               '<MESSAGE_HEADER Msg_type="MEDIA_DATA_MESSAGE" Msg_code="2" Msg_flag="0" />\n'
               '<MESSAGE_BODY>\n<POS FileStartPos="0" StartPos="0" DataLen="10" />\n'
               '</MESSAGE_BODY>\n</XML_TOPSEE>').encode("gb2312")
        data_frame = MAGIC + struct.pack("<I", len(env)+4+10) + env + b"\x00\x00\x00\x00" + b"0123456789"
        eof_env = env.replace(b'DataLen="10"', b'DataLen="0"')
        eof = MAGIC + struct.pack("<I", len(eof_env)) + eof_env
        inbound = (MAGIC + struct.pack("<I", len(resp)) + resp) + data_frame + eof
        c = AnjoyCommClient("x"); c.sock = _FakeSock(inbound); c.sessionid = "S"
        with self.assertRaises(AnjoyError):
            c.download_file("/mnt/nand/config.xml")


    def test_snapshot_triggers_and_downloads(self):
        with FakeCommServer() as srv:
            c = AnjoyCommClient("127.0.0.1", "admin", "123456", port=srv.port)
            c.connect(); c.login()
            jpg = c.snapshot(stream=0, quality=90)
            c.close()
        self.assertEqual(jpg, b"\xff\xd8\xffFAKEJPEG\xff\xd9")
        types = [t for t, _ in srv.received]
        self.assertIn("SYSTEM_CONTROL_MESSAGE", types)   # 1043 trigger + 1023 download

    def test_snapshot_no_jpgfile_raises(self):
        from anjoy.exceptions import AnjoyError
        from anjoy.comm import MAGIC
        resp = build_envelope("SYSTEM_CONTROL_MESSAGE", "1043",
                              "<RESPONSE_PARAM></RESPONSE_PARAM>")
        inbound = MAGIC + struct.pack("<I", len(resp)) + resp
        c = AnjoyCommClient("x"); c.sock = _FakeSock(inbound); c.sessionid = "S"
        with self.assertRaises(AnjoyError):
            c.snapshot()


    def test_snapshot_rejects_bad_params(self):
        c = AnjoyCommClient("x"); c.sock = _FakeSock()
        with self.assertRaises(ValueError):
            c.snapshot(stream=2)
        with self.assertRaises(ValueError):
            c.snapshot(quality=0)


    def test_reboot_sends_1007(self):
        c = AnjoyCommClient("x"); c.sock = _FakeSock(); c.sessionid = "S"
        c.reboot()
        self.assertIn(b'Msg_type="SYSTEM_CONTROL_MESSAGE"', c.sock.sent)
        self.assertIn(b'Msg_code="1007"', c.sock.sent)
        self.assertIn(b"<MESSAGE_BODY/>", c.sock.sent)   # empty body


    def test_set_config_section_frame_and_ack(self):
        from anjoy.comm import MAGIC
        from anjoy import const
        body = '<Overlay Enable="1"><TitleOverlay TitleUtf8="54657374"/></Overlay>'
        ack = build_envelope("SYSTEM_CONFIG_SET_MESSAGE", const.CFG_OVERLAY)   # empty body = ack
        inbound = MAGIC + struct.pack("<I", len(ack)) + ack
        c = AnjoyCommClient("x"); c.sock = _FakeSock(inbound); c.sessionid = "S"
        c.set_config_section(const.CFG_OVERLAY, body, confirm=True)
        self.assertIn(b'Msg_type="SYSTEM_CONFIG_SET_MESSAGE"', c.sock.sent)
        self.assertIn(b'Msg_code="525"', c.sock.sent)
        self.assertIn(b'<TitleOverlay TitleUtf8="54657374"/>', c.sock.sent)

    def test_set_config_section_requires_confirm(self):
        from anjoy.exceptions import AnjoyError
        c = AnjoyCommClient("x"); c.sock = _FakeSock()
        with self.assertRaises(AnjoyError):
            c.set_config_section("525", "<Overlay/>")
        self.assertEqual(c.sock.sent, b"")               # nothing written to the camera

    def test_set_config_section_rejects_non_gb2312(self):
        from anjoy.exceptions import AnjoyError
        c = AnjoyCommClient("x"); c.sock = _FakeSock()
        with self.assertRaises(AnjoyError):
            c.set_config_section("525", '<Overlay Title="\U0001F4C1"/>', confirm=True)

    def test_set_config_section_roundtrip(self):
        from anjoy import const
        with FakeCommServer() as srv:
            c = AnjoyCommClient("127.0.0.1", "admin", "123456", port=srv.port)
            c.connect(); c.login()
            c.set_config_section(
                const.CFG_OVERLAY,
                '<Overlay><TitleOverlay TitleUtf8="54657374"/></Overlay>',
                confirm=True)
            c.close()
        self.assertEqual(len(srv.config_sets), 1)
        code, sect = srv.config_sets[0]
        self.assertEqual(code, "525")
        self.assertIn(b'TitleUtf8="54657374"', sect)


if __name__ == "__main__":
    unittest.main()
