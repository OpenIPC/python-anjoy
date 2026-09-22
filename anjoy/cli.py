"""Command-line interface for python-anjoy.

    anjoy discover
    anjoy 192.168.0.123 info
    anjoy 192.168.0.123 ptz right --speed 5
    anjoy 192.168.0.123 ptz stop
    anjoy 192.168.0.123 preset goto 94
    anjoy 192.168.0.123 config /getPtzConfig
    anjoy 192.168.0.123 rtsp out.mp4 --duration 5 --stream 0
    anjoy 192.168.0.123 call /getSystemVersionInfo
"""

from __future__ import annotations

import argparse
import json
import sys

from . import const, rtsp
from .client import AnjoyClient
from .discovery import discover


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="anjoy", description="Anjoy IP camera client")
    ap.add_argument("host", nargs="?", help="camera IP (omit for 'discover')")
    ap.add_argument("-u", "--user", default=const.DEFAULT_USER)
    ap.add_argument("-P", "--password", default=const.DEFAULT_PASSWORD)
    ap.add_argument("-p", "--port", type=int, default=const.WEB_PORT)
    ap.add_argument("-v", "--verbose", action="store_true")

    sub = ap.add_subparsers(dest="command")

    p_disc = sub.add_parser("discover", help="find Anjoy cameras on the LAN")
    p_disc.add_argument("--timeout", type=float, default=3.0)
    p_disc.add_argument("--interface", help="pin a NIC, e.g. eth0")

    sub.add_parser("info", help="device version + chip + app type")

    p_cfg = sub.add_parser("config", help="GET a config endpoint, or --set a body")
    p_cfg.add_argument("endpoint", help="e.g. /getPtzConfig")
    p_cfg.add_argument("--set", metavar="XML", help="raw config XML body to write")

    sub.add_parser("users", help="list users")

    p_ptz = sub.add_parser("ptz", help="control PTZ")
    p_ptz.add_argument("action", help="up/down/left/right/(diagonals)/stop, "
                       "zoom/focus/iris, or ptz-config")
    p_ptz.add_argument("value", nargs="?", help="tele|wide / near|far / open|close")
    p_ptz.add_argument("--speed", type=int, default=4)

    p_pre = sub.add_parser("preset", help="preset set|goto|del N")
    p_pre.add_argument("op", choices=["set", "goto", "del"])
    p_pre.add_argument("number", type=int)

    sub.add_parser("reboot", help="reboot the camera")

    p_rt = sub.add_parser("rtsp", help="record live video over RTSP (ffmpeg)")
    p_rt.add_argument("output", nargs="?", default="rtsp.mp4")
    p_rt.add_argument("--stream", type=int, default=0, choices=(0, 1),
                      help="0=MainStream, 1=SubStream (current-gen Anjoy)")
    p_rt.add_argument("--duration", type=float, default=10.0)
    p_rt.add_argument("--legacy-template", metavar="TMPL",
                      help="use the legacy /live-style path + userinfo auth "
                           "(older firmware); e.g. '/live/{channel}_{subtype}'")
    p_rt.add_argument("--channel", type=int, default=0, help="legacy template only")
    p_rt.add_argument("--subtype", type=int, default=0, help="legacy template only")

    p_call = sub.add_parser("call", help="raw endpoint call")
    p_call.add_argument("endpoint")
    p_call.add_argument("--body", default="", help="raw SOAP body XML")

    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "discover" or (args.command is None and args.host == "discover"):
        timeout = getattr(args, "timeout", 3.0)
        interface = getattr(args, "interface", None)
        _print(discover(timeout=timeout, interface=interface))
        return 0

    if not args.host:
        build_parser().error("host is required (or use 'discover')")
    if not args.command:
        build_parser().error("a subcommand is required")

    cam = AnjoyClient(args.host, args.user, args.password, port=args.port)

    try:
        if args.command == "info":
            _print(cam.info())
        elif args.command == "config":
            _print(cam.set_config(args.endpoint, args.set) if args.set
                   else cam.get_config(args.endpoint))
        elif args.command == "users":
            _print(cam.get_users())
        elif args.command == "ptz":
            _print(_ptz(cam, args))
        elif args.command == "preset":
            fn = {"set": cam.preset_set, "goto": cam.preset_goto,
                  "del": cam.preset_del}[args.op]
            _print(fn(args.number))
        elif args.command == "reboot":
            _print(cam.reboot())
        elif args.command == "rtsp":
            if args.legacy_template:
                url = rtsp.build_rtsp_url(args.host, args.user, args.password,
                                          channel=args.channel, subtype=args.subtype,
                                          template=args.legacy_template)
            else:
                url = rtsp.anjoy_rtsp_url(args.host, args.user, args.password,
                                          stream=args.stream)
            print(f"recording {url} -> {args.output}", file=sys.stderr)
            rtsp.record_rtsp(url, args.output, args.duration)
            _print({"output": args.output, "url": url})
        elif args.command == "call":
            _print(cam.call(args.endpoint, args.body))
    except Exception as e:  # noqa: BLE001
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


def _ptz(cam: AnjoyClient, args):
    a = args.action
    if a in const.PTZ_DIRECTIONS:
        return cam.ptz_move(a, args.speed)
    if a == "stop":
        return cam.ptz_stop()
    if a == "zoom":
        return cam.ptz_zoom(args.value or "tele")
    if a == "focus":
        return cam.ptz_focus(args.value or "far")
    if a == "iris":
        return cam.ptz_iris(args.value or "open")
    if a in ("ptz-config", "config"):
        return cam.get_ptz_config()
    raise SystemExit(f"unknown ptz action {a!r}")


if __name__ == "__main__":
    raise SystemExit(main())
