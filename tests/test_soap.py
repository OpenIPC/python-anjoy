"""SOAP transport: auth header, envelope, response parsing, error raising."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from anjoy import const
from anjoy.des import des_hex
from anjoy.exceptions import AnjoyAPIError
from anjoy.soap import AnjoySOAPTransport, extract
from tests.fake_server import FakeAnjoyHTTPServer


class TestExtract(unittest.TestCase):
    def test_json(self):
        self.assertEqual(extract('{"result":true}'), {"result": True})

    def test_xml_fragment(self):
        out = extract('<PTZConfig Protocol="PELCO_D" ComPort="1" />')
        self.assertEqual(out["PTZConfig"]["Protocol"], "PELCO_D")

    def test_empty(self):
        self.assertIsNone(extract(""))

    def test_unparseable_returns_raw(self):
        self.assertEqual(extract("not xml <<"), "not xml <<")


class TestSOAPTransport(unittest.TestCase):
    def test_auth_header_and_body(self):
        seen = {}

        def echo(body):
            seen["body"] = body
            return '<PTZConfig Protocol="PELCO_D" />'

        with FakeAnjoyHTTPServer({const.GET_PTZ_CONFIG: echo}) as srv:
            t = AnjoySOAPTransport("127.0.0.1", "admin", "123456", port=srv.port)
            out = t.call(const.GET_PTZ_CONFIG,
                         "<xml><cmd>right</cmd></xml>")
            self.assertEqual(out["PTZConfig"]["Protocol"], "PELCO_D")
            # auth header carried the DES-hex of the creds
            self.assertEqual(srv.auth_seen[-1],
                             (des_hex("admin"), des_hex("123456")))
            self.assertEqual(srv.auth_seen[-1], srv.expected_auth)
            # body was forwarded intact
            self.assertIn("right", seen["body"])

    def test_error_envelope_raises(self):
        with FakeAnjoyHTTPServer(
                {const.PTZ_CMD: lambda b: '{"result":false,"error":'
                 '{"code":285475072,"message":"no ptz"}}'}) as srv:
            t = AnjoySOAPTransport("127.0.0.1", port=srv.port)
            with self.assertRaises(AnjoyAPIError) as ctx:
                t.call(const.PTZ_CMD, "<xml><cmd>right</cmd></xml>")
            self.assertEqual(ctx.exception.code, 285475072)

    def test_request_never_raises_on_error_body(self):
        with FakeAnjoyHTTPServer(
                {const.PTZ_CMD: lambda b: '{"result":false,"error":{"code":1}}'}) as srv:
            t = AnjoySOAPTransport("127.0.0.1", port=srv.port)
            # request() returns the raw text without raising
            self.assertIn("result", t.request(const.PTZ_CMD))


if __name__ == "__main__":
    unittest.main()
