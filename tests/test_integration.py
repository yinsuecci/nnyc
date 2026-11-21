import asyncio
import ssl
import tempfile
import unittest
from contextlib import AsyncExitStack
from pathlib import Path

from aiohttp import ClientResponseError, web

from peer import Peer, exchange, listener
from provision_demo import provision
from signal_server import application


class IntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack = AsyncExitStack()
        self.addAsyncCleanup(self.stack.aclose)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())) / "demo"
        self.configs = provision(root)
        self.now = 1.0
        server = self.configs["server"]
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.load_cert_chain(server["cert"], server["key"])
        runner = web.AppRunner(application(server["devices"], lambda: self.now, relay=True,
                                            **getattr(self, "app_options", {})))
        await runner.setup()
        self.stack.push_async_callback(runner.cleanup)
        self.site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=context)
        await self.site.start()
        port = runner.addresses[0][1]
        for name in ("alice", "bob"):
            config = self.configs[name]
            config["signaling"] = f"https://127.0.0.1:{port}"
            client = await self.stack.enter_async_context(Peer(config, (root / name / "token").read_text()))
            setattr(self, name, client)
        await self.alice.call("heartbeat", port=9444)

    async def rejected(self, status, client, action, **fields):
        with self.assertRaises(ClientResponseError) as error:
            await client.call(action, **fields)
        self.assertEqual(error.exception.status, status)

    async def request(self):
        return (await self.alice.call("request", target="bob"))["id"]

    async def open_listener(self):
        port, allow, accepted, stop = await listener(self.bob, "alice", "127.0.0.1", 0)
        self.stack.push_async_callback(stop)
        await self.bob.call("heartbeat", port=port)
        return allow, accepted

    async def test_authorized_direct_tls_exchange_and_replay(self):
        allow, accepted = await self.open_listener()
        sid = await self.request()
        self.assertEqual((await self.bob.call("inbox"))["pending"], [{"id": sid, "source": "alice"}])
        await self.rejected(409, self.alice, "endpoint", id=sid)
        await self.rejected(403, self.alice, "approve", id=sid)
        allow(sid)
        await self.bob.call("approve", id=sid)
        await self.rejected(409, self.bob, "approve", id=sid)
        data = b"nn-remote encrypted payload\x00"
        self.assertEqual(await exchange(self.alice, sid, "bob", data), data)
        self.assertEqual(await asyncio.wait_for(accepted, 2), data)
        await self.rejected(409, self.bob, "claim", id=sid)
        await self.rejected(409, self.alice, "endpoint", id=sid)

    async def test_wrong_peer_certificate(self):
        allow, accepted = await self.open_listener()
        sid = await self.request()
        allow(sid)
        await self.bob.call("approve", id=sid)
        self.alice.config["peers"]["bob"] = self.alice.config["cert"]
        loop = asyncio.get_running_loop()
        original_handler = loop.get_exception_handler()

        def handle_expected_rejection(loop, context):
            if (context.get("message") == "Error on transport creation for incoming connection"
                    and isinstance(context.get("exception"), ConnectionResetError)):
                return
            loop.default_exception_handler(context)

        self.addCleanup(loop.set_exception_handler, original_handler)
        loop.set_exception_handler(handle_expected_rejection)
        with self.assertRaises(ssl.SSLCertVerificationError):
            await exchange(self.alice, sid, "bob", b"forbidden")
        self.assertFalse(accepted.done())

    async def test_denial_expiry_and_disconnect(self):
        await self.open_listener()
        sid = await self.request()
        await self.bob.call("deny", id=sid)
        await self.rejected(404, self.alice, "endpoint", id=sid)
        sid = await self.request()
        self.now += 15
        await self.rejected(404, self.bob, "approve", id=sid)
        sid = await self.request()
        await self.bob.call("approve", id=sid)
        await self.bob.call("leave")
        await self.rejected(404, self.alice, "endpoint", id=sid)

    async def test_approved_expiry_and_heartbeat_cleanup(self):
        await self.open_listener()
        sid = await self.request()
        await self.bob.call("approve", id=sid)
        self.now += 15
        await self.rejected(404, self.bob, "claim", id=sid)
        self.now += 5
        await self.rejected(409, self.alice, "request", target="bob")
        await self.alice.call("heartbeat", port=9444)
        await self.rejected(404, self.alice, "request", target="bob")

    async def test_authentication_and_rate_limit(self):
        bad = await self.stack.enter_async_context(Peer(self.configs["alice"], "0" * 64))
        await self.rejected(401, bad, "heartbeat", port=9444)
        for _ in range(7):
            await self.alice.call("heartbeat", port=9444)
        await self.rejected(429, self.alice, "heartbeat", port=9444)
        self.now += 1
        await self.alice.call("heartbeat", port=9444)
