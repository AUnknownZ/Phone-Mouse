"""HTTP(S) + WebSocket server that the phone connects to."""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import secrets
import socket
import ssl
from pathlib import Path
from typing import Callable

from aiohttp import WSMsgType, web

from .controller import Controller
from .mouse import MouseBackend

STATIC_DIR = Path(__file__).parent / "static"
CERT_DIR = Path.home() / ".phone-mouse"

log = logging.getLogger("phone_mouse")

MOUSE_FACTORY = web.AppKey("mouse_factory", Callable[[], MouseBackend])
TOKEN = web.AppKey("token", str)


def lan_ip() -> str:
    """Best guess at this computer's address on the local network."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            # No packets are sent; this just asks the OS which interface
            # would route to a public address.
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "index.html")


async def websocket(request: web.Request) -> web.WebSocketResponse:
    token = request.app[TOKEN]
    supplied = request.query.get("token", "")
    if not hmac.compare_digest(supplied.encode(), token.encode()):
        raise web.HTTPForbidden(text="bad pairing token")

    ws = web.WebSocketResponse(heartbeat=10)
    await ws.prepare(request)
    controller = Controller(request.app[MOUSE_FACTORY]())
    peer = request.remote
    log.info("phone connected from %s", peer)
    await ws.send_json({"type": "hello"})

    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                payload = json.loads(msg.data)
            except ValueError:
                continue
            try:
                reply = controller.handle(payload)
            except Exception:
                log.exception("failed to handle %r", payload)
                continue
            if reply is not None:
                await ws.send_json(reply)
    finally:
        controller.release_all()
        log.info("phone disconnected from %s", peer)
    return ws


def create_app(mouse_factory: Callable[[], MouseBackend], token: str) -> web.Application:
    app = web.Application()
    app[MOUSE_FACTORY] = mouse_factory
    app[TOKEN] = token
    app.router.add_get("/", index)
    app.router.add_get("/ws", websocket)
    app.router.add_static("/static/", STATIC_DIR)
    return app


def _print_banner(url: str) -> None:
    print()
    print("  Phone Mouse is running.")
    print("  On your phone (same Wi-Fi), open:")
    print()
    print(f"    {url}")
    print()
    try:
        import qrcode

        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.print_ascii(invert=True)
    except Exception:
        pass
    if url.startswith("https"):
        print("  The certificate is self-signed: in Safari tap 'Show Details' >")
        print("  'visit this website' once to trust it.")
    print("  Press Ctrl+C to stop.")
    print(flush=True)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="phone_mouse", description="Use your phone as a mouse for this computer."
    )
    parser.add_argument("--host", default="0.0.0.0", help="address to listen on")
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument(
        "--no-https",
        action="store_true",
        help="serve plain HTTP (motion sensors will not work on iOS; touchpad still does)",
    )
    parser.add_argument("--token", help="fixed pairing token (default: random each run)")
    parser.add_argument(
        "--dry-run", action="store_true", help="log mouse actions instead of moving the cursor"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.dry_run:
        from .mouse import RecordingMouse

        def factory() -> MouseBackend:
            return RecordingMouse(verbose=True)
    else:
        from .mouse import PynputMouse

        PynputMouse()  # fail fast if the OS refuses mouse control

        def factory() -> MouseBackend:
            return PynputMouse()

    token = args.token or secrets.token_urlsafe(6)
    ip = lan_ip()

    ssl_context = None
    scheme = "http"
    if not args.no_https:
        from .certs import ensure_certificate

        cert, key = ensure_certificate(CERT_DIR, [ip])
        ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_context.load_cert_chain(cert, key)
        scheme = "https"

    _print_banner(f"{scheme}://{ip}:{args.port}/?token={token}")
    web.run_app(
        create_app(factory, token),
        host=args.host,
        port=args.port,
        ssl_context=ssl_context,
        print=None,
    )
