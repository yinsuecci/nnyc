import asyncio
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from aiohttp import ClientError, WSServerHandshakeError, web
from PIL import Image

import test_integration as base
from desktop_stream import encode_frame, host_desktop, view_desktop
from file_transfer import FileReceiver, receive_file, send_file
from relay_client import websocket
from signal_server import STATE


class ProductionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = base.IntegrationTest.asyncSetUp
    rejected = base.IntegrationTest.rejected

    async def test_pinned_desktop_frames_input_and_device_revocation(self):
        events = asyncio.Queue()
        shown = asyncio.Queue()
        released = asyncio.Event()

        class Backend:
            def capture(self):
                return encode_frame(Image.new("RGB", (128, 72), "#2855d9"))

            def handle(self, kind, args):
                events.put_nowait((kind, args))

            def release(self):
                released.set()

        class View:
            def __init__(self):
                self.events = asyncio.Queue()

            def connected(self, fps, allow_input):
                self.settings = (fps, allow_input)

            def show(self, image):
                if shown.empty():
                    shown.put_nowait(image)

        receiver = FileReceiver(self.bob, lambda _: None)
        port = await receiver.start("127.0.0.1", 0)
        self.stack.push_async_callback(receiver.close)
        await self.bob.call("heartbeat", port=port)
        await self.rejected(422, self.alice, "request", target="bob", purpose="desktop")
        await self.bob.call("heartbeat", port=port, desktop=True)
        sid = (await self.alice.call("request", target="bob", purpose="desktop"))["id"]
        await self.rejected(409, self.alice, "endpoint", id=sid)

        async def serve(reader, writer):
            await host_desktop(reader, writer, Backend(), 60, True)

        await receiver.allow_service(sid, "alice", "desktop", serve)
        view = View()
        viewer = asyncio.create_task(view_desktop(self.alice, "bob", sid, view))
        try:
            image = await asyncio.wait_for(shown.get(), 5)
            self.assertEqual(image.size, (128, 72))
            self.assertEqual(view.settings, (60, True))
            view.events.put_nowait(["key", "a", True])
            self.assertEqual(await asyncio.wait_for(events.get(), 2), ("key", ["a", True]))
            rendezvous = self.site._runner.app[STATE]
            rendezvous.update_registry({key: name for key, name in rendezvous.devices.items()
                                       if name != "alice"})
            with self.assertRaises((ConnectionError, EOFError, ClientError)):
                await asyncio.wait_for(viewer, 5)
            await asyncio.wait_for(released.wait(), 2)
            await self.rejected(401, self.alice, "heartbeat", port=9444)
            await self.rejected(404, self.bob, "active", id=sid)
        finally:
            viewer.cancel()
            await asyncio.gather(viewer, return_exceptions=True)

    async def test_registry_atomic_rotation_and_health_over_tls(self):
        from aiohttp import ClientSession, web
        from signal_server import application

        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        original = self.configs["server"]["devices"]
        registry = root / "registry.json"
        registry.write_text(json.dumps(original))
        tls = self.site._ssl_context
        runner = web.AppRunner(application(original, registry_file=registry))
        await runner.setup()
        self.stack.push_async_callback(runner.cleanup)
        await web.TCPSite(runner, "127.0.0.1", 0, ssl_context=tls).start()
        url = f"https://127.0.0.1:{runner.addresses[0][1]}/healthz"

        async def expect_status(client, status):
            async with asyncio.timeout(5):
                while True:
                    async with client.get(url, ssl=self.alice.signaling_tls) as response:
                        if response.status == status:
                            return
                    await asyncio.sleep(0.05)

        async with ClientSession() as client:
            await expect_status(client, 200)
            update = root / "replace.json"
            update.write_text("{}")
            os.replace(update, registry)
            await expect_status(client, 503)
            update.write_text(json.dumps(original))
            os.replace(update, registry)
            await expect_status(client, 200)

    async def test_incomplete_file_or_revoked_commit_does_not_publish(self):
        directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        def payload(data):
            return len(data).to_bytes(4, "big") + data

        async def revoked():
            raise PermissionError("lease ended")

        for content, error in ((b"partial", EOFError), (b"complete!", PermissionError)):
            reader = asyncio.StreamReader()
            reader.feed_data(payload(b'{"name":"never.txt","size":9}') + payload(content)
                             + (payload(hashlib.sha256(content).digest())
                                if content == b"complete!" else b""))
            reader.feed_eof()
            with self.assertRaises(error):
                await receive_file(reader, None, directory, revoked)
            self.assertEqual(list(directory.iterdir()), [])

    async def test_authorized_relay_fallback_carries_only_inner_tls(self):
        from unittest.mock import patch

        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        source = root / "source"
        source.mkdir()
        path = source / "test-data.bin"
        marker = b"nn-relay-private-test-marker"
        path.write_bytes(marker * 400)
        for client in (self.alice, self.bob):
            client.config["relay"] = True
        receiver = FileReceiver(self.bob, lambda _: None)
        port = await receiver.start("127.0.0.1", 0)
        self.stack.push_async_callback(receiver.close)
        await self.bob.call("heartbeat", port=port)
        sid = (await self.alice.call("request", target="bob", purpose="file"))["id"]
        with self.assertRaises(WSServerHandshakeError) as denied:
            async with websocket(self.alice, sid):
                self.fail("unapproved relay was accessible")
        self.assertEqual(denied.exception.status, 409)
        await receiver.allow(sid, "alice", root)

        # Keep a bound but non-listening port to deterministically force direct connect failure.
        import socket
        with socket.socket() as unused:
            unused.bind(("127.0.0.1", 0))
            blocked = unused.getsockname()[1]
            original = self.alice.call
            async def unreachable(action, **fields):
                response = await original(action, **fields)
                if action == "endpoint":
                    response["port"] = blocked
                return response

            self.alice.call = unreachable
            records = []
            send_bytes = web.WebSocketResponse.send_bytes
            async def record(socket, data, *args, **kwargs):
                records.append(data)
                await send_bytes(socket, data, *args, **kwargs)

            with patch.object(web.WebSocketResponse, "send_bytes", record):
                await asyncio.wait_for(send_file(self.alice, "bob", sid, path), 12)
        self.assertEqual((root / path.name).read_bytes(), path.read_bytes())
        self.assertTrue(records)
        self.assertNotIn(marker, b"".join(records))
