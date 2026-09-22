"""RTSP URL construction + optional ffmpeg record/iter helpers.

RTSP is served on port 554 with HTTP-Basic-style auth (config ``Auth="1"``). The
exact path template is confirmed against a live unit; the default below is a
placeholder that :func:`build_rtsp_url` lets callers override with ``template``.
"""

from __future__ import annotations

import shutil
import subprocess
import urllib.parse

from . import const

# Placeholder — confirm the real path on a live sample (Phase B).
DEFAULT_TEMPLATE = "/live/{channel}_{subtype}"


def build_rtsp_url(host: str, user: str = const.DEFAULT_USER,
                   password: str = const.DEFAULT_PASSWORD, *,
                   channel: int = 0, subtype: int = 0,
                   port: int = const.RTSP_PORT, template: str = DEFAULT_TEMPLATE) -> str:
    """Build ``rtsp://user:pass@host:port<path>``. *template* accepts
    ``{channel}`` / ``{subtype}`` placeholders."""
    path = template.format(channel=channel, subtype=subtype)
    if not path.startswith("/"):
        path = "/" + path
    cred = ""
    if user:
        cred = f"{urllib.parse.quote(user, safe='')}:{urllib.parse.quote(password, safe='')}@"
    return f"rtsp://{cred}{host}:{port}{path}"


def record_rtsp(url: str, output: str, duration: float = 10.0,
                transport: str = "tcp") -> None:
    """Record *duration* seconds of *url* to *output* via ffmpeg (stream copy)."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")
    subprocess.run(
        ["ffmpeg", "-y", "-rtsp_transport", transport, "-i", url,
         "-t", str(duration), "-c", "copy", output],
        check=True)


def iter_rtsp(url: str, transport: str = "tcp"):
    """Yield raw MPEG-TS chunks from *url* via ffmpeg (for live processing)."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")
    proc = subprocess.Popen(
        ["ffmpeg", "-rtsp_transport", transport, "-i", url,
         "-c", "copy", "-f", "mpegts", "pipe:1"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                break
            yield chunk
    finally:
        proc.terminate()
        proc.wait()
