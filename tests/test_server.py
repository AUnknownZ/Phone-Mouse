import asyncio
import ssl

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestClient, TestServer

from phone_mouse.certs import ensure_certificate
from phone_mouse.mouse import RecordingMouse
from phone_mouse.server import create_app

TOKEN = "secret-token"


@pytest.fixture
async def client_and_mice():
    mice: list[RecordingMouse] = []

    def factory():
        mice.append(RecordingMouse())
        return mice[-1]

    client = TestClient(TestServer(create_app(factory, TOKEN)))
    await client.start_server()
    yield client, mice
    await client.close()


async def wait_for(predicate, timeout=2.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


async def test_serves_phone_page(client_and_mice):
    client, _ = client_and_mice
    resp = await client.get("/")
    assert resp.status == 200
    assert "Phone Mouse" in await resp.text()
    resp = await client.get("/static/app.js")
    assert resp.status == 200


async def test_websocket_requires_token(client_and_mice):
    client, mice = client_and_mice
    for query in ("", "?token=wrong"):
        resp = await client.get("/ws" + query)
        assert resp.status == 403
    assert mice == []


async def test_websocket_drives_mouse_and_releases_on_disconnect(client_and_mice):
    client, mice = client_and_mice
    ws = await client.ws_connect(f"/ws?token={TOKEN}")
    assert (await ws.receive_json())["type"] == "hello"

    await ws.send_json({"type": "settings", "mode": "touch"})
    await ws.send_json({"type": "move", "dx": 10, "dy": 5})
    await ws.send_json({"type": "button", "button": "left", "action": "down"})
    await ws.send_str("not json")
    await ws.send_json({"type": "ping", "t": 7})
    reply = await ws.receive_json()
    while reply["type"] == "status":
        reply = await ws.receive_json()
    assert reply == {"type": "pong", "t": 7}

    mouse = mice[0]
    assert ("move", 20, 10) in mouse.events
    assert mouse.held == {"left"}

    await ws.close()
    await wait_for(lambda: mouse.held == set())
    assert mouse.events[-1] == ("release", "left")


async def test_https_with_generated_certificate(tmp_path):
    cert, key = ensure_certificate(tmp_path, ["127.0.0.1"])
    server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_ctx.load_cert_chain(cert, key)

    runner = web.AppRunner(create_app(RecordingMouse, TOKEN))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=server_ctx)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    client_ctx = ssl.create_default_context(cafile=str(cert))
    try:
        async with ClientSession() as session:
            async with session.ws_connect(
                f"wss://127.0.0.1:{port}/ws?token={TOKEN}", ssl=client_ctx
            ) as ws:
                assert (await ws.receive_json())["type"] == "hello"
    finally:
        await runner.cleanup()

    # A second call reuses the existing certificate.
    assert ensure_certificate(tmp_path, ["127.0.0.1"]) == (cert, key)
    before = cert.read_bytes()
    ensure_certificate(tmp_path, ["10.1.2.3"])
    assert cert.read_bytes() != before
