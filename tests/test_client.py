"""AnjoyClient method surface against the fake HTTP server."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from anjoy import const
from anjoy.client import AnjoyClient, _xml
from tests.fake_server import FakeAnjoyHTTPServer


def _bodies_for(srv, endpoint):
    return [b for (ep, b) in srv.received if ep == endpoint]


class TestClient(unittest.TestCase):
    def test_info_gathers_three_calls(self):
        handlers = {
            const.GET_VERSION: lambda b: '<Version Build="V1.0" />',
            const.GET_CHIP_UUID: lambda b: '<Chip uuid="DEADBEEF" />',
            const.GET_APP_TYPE: lambda b: '{"app":"ipc"}',
        }
        with FakeAnjoyHTTPServer(handlers) as srv:
            cam = AnjoyClient("127.0.0.1", port=srv.port)
            info = cam.info()
            self.assertEqual(info["version"]["Version"]["Build"], "V1.0")
            self.assertEqual(info["chip_uuid"]["Chip"]["uuid"], "DEADBEEF")
            self.assertEqual(info["app_type"], {"app": "ipc"})

    def test_ptz_move_and_stop_bodies(self):
        with FakeAnjoyHTTPServer({const.PTZ_CMD: lambda b: '{"result":true}'}) as srv:
            cam = AnjoyClient("127.0.0.1", port=srv.port)
            cam.ptz_move("right", speed=5)
            cam.ptz_stop()
            bodies = _bodies_for(srv, const.PTZ_CMD)
            self.assertIn("<cmd>right</cmd>", bodies[0])
            self.assertIn("<panspeed>5</panspeed>", bodies[0])
            self.assertIn("<cmd>stop</cmd>", bodies[1])

    def test_ptz_lens_verbs(self):
        with FakeAnjoyHTTPServer({const.PTZ_CMD: lambda b: '{"result":true}'}) as srv:
            cam = AnjoyClient("127.0.0.1", port=srv.port)
            cam.ptz_zoom("tele")
            cam.ptz_focus("near")
            bodies = _bodies_for(srv, const.PTZ_CMD)
            self.assertIn("<cmd>zoomtele</cmd>", bodies[0])
            self.assertIn("<cmd>FocusNearAutoOff</cmd>", bodies[1])

    def test_ptz_bad_direction(self):
        cam = AnjoyClient("127.0.0.1")
        with self.assertRaises(ValueError):
            cam.ptz_move("diagonal-nonsense")

    def test_preset_bodies(self):
        with FakeAnjoyHTTPServer({const.PRESET_LIST: lambda b: '{"result":true}'}) as srv:
            cam = AnjoyClient("127.0.0.1", port=srv.port)
            cam.preset_set(94)
            cam.preset_goto(94)
            cam.preset_del(94)
            bodies = _bodies_for(srv, const.PRESET_LIST)
            self.assertIn("<cmd>setpreset</cmd>", bodies[0])
            self.assertIn("<flag>1</flag>", bodies[0])
            self.assertIn("<cmd>callpreset</cmd>", bodies[1])
            self.assertIn("<cmd>delpreset</cmd>", bodies[2])

    def test_xml_builder(self):
        self.assertEqual(_xml("right", panspeed=4, tiltspeed=4),
                         "<xml><cmd>right</cmd><panspeed>4</panspeed>"
                         "<tiltspeed>4</tiltspeed></xml>")


class TestAsyncClient(unittest.TestCase):
    def test_async_ptz(self):
        import asyncio
        from anjoy.aio import AsyncAnjoyClient
        with FakeAnjoyHTTPServer({const.PTZ_CMD: lambda b: '{"result":true}'}) as srv:
            async def run():
                cam = AsyncAnjoyClient("127.0.0.1", port=srv.port)
                await cam.ptz_move("left")
                await cam.ptz_stop()
            asyncio.run(run())
            bodies = _bodies_for(srv, const.PTZ_CMD)
            self.assertIn("<cmd>left</cmd>", bodies[0])
            self.assertIn("<cmd>stop</cmd>", bodies[1])


if __name__ == "__main__":
    unittest.main()
