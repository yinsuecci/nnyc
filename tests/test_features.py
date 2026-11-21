import asyncio
import hashlib
from importlib.util import find_spec
import os
import ssl
import sys
import tempfile
import unittest
from pathlib import Path

from aiohttp import ClientSession, CookieJar, WSMsgType, WSServerHandshakeError, web

from file_transfer import FileReceiver, send_file, valid_name
from provision_demo import certificate
from terminal_portal import Portal

import test_integration as base


class FileTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = base.IntegrationTest.asyncSetUp
    rejected = base.IntegrationTest.rejected

    async def test_bidirectional_file_transfer_and_existing_file(self):
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        notices = asyncio.Queue()
        for client in (self.alice, self.bob):
            receiver = FileReceiver(client, notices.put_nowait)
            port = await receiver.start("127.0.0.1", 0)
            await client.call("heartbeat", port=port)
            client.receiver = receiver
            self.stack.push_async_callback(receiver.close)
        self.assertEqual((await self.alice.call("devices", peers=["bob", "unknown"]))["devices"],
                         {"bob": True})
        for sender, target in (("alice", "bob"), ("bob", "alice")):
            self.now += 1
            client = getattr(self, sender)
            remote = getattr(self, target)
            source = root / sender
            source.mkdir(exist_ok=True)
            destination = root / target
            destination.mkdir(exist_ok=True)
            path = source / f"{sender}-测试.bin"
            data = os.urandom(190000)
            path.write_bytes(data)
            sid = (await client.call("request", target=target, purpose="file"))["id"]
            request = (await remote.call("inbox"))["pending"][0]
            self.assertEqual(request, {"id": sid, "source": sender, "purpose": "file"})
            await self.rejected(409, client, "endpoint", id=sid)
            await remote.receiver.allow(sid, sender, destination)
            digest = await send_file(client, target, sid, path)
            self.assertEqual(digest, hashlib.sha256(data).hexdigest())
            self.assertEqual((destination / path.name).read_bytes(), data)
            self.assertEqual(await asyncio.wait_for(notices.get(), 1), f"收到文件：{path.name}")
            await self.rejected(409, client, "endpoint", id=sid)
            self.now += 1
            duplicate = (await client.call("request", target=target, purpose="file"))["id"]
            await remote.receiver.allow(duplicate, sender, destination)
            path.write_bytes(b"different data")
            with self.assertRaises(EOFError):
                await send_file(client, target, duplicate, path)
            self.assertEqual((destination / path.name).read_bytes(), data)
            self.assertFalse(list(destination.glob(".nn-transfer-*")))
            self.assertIn("FileExistsError", await asyncio.wait_for(notices.get(), 1))

    def test_cross_platform_name_validation(self):
        for name in ("..", "dir/file", "AUX.txt", ".hidden", "COM¹.txt", "ok. ", "x\x00"):
            self.assertFalse(valid_name(name))
        self.assertTrue(valid_name("课件说明.pdf"))


@unittest.skipIf(os.name == "nt" and find_spec("winpty") is None,
                 "Windows requires pywinpty")
class TerminalTests(unittest.IsolatedAsyncioTestCase):
    async def test_https_pair_origin_replay_and_persistent_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            certificate(root, "localhost")
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls.minimum_version = ssl.TLSVersion.TLSv1_3
            tls.load_cert_chain(root / "cert.pem", root / "key.pem")
            trust = ssl.create_default_context(cafile=str(root / "cert.pem"))
            trust.minimum_version = ssl.TLSVersion.TLSv1_3
            script = (
                "import os; print('agent-ready', flush=True); "
                "exec('for line in __import__(\"sys\").stdin:\\n print(\"agent-ack:\" + line.strip() + "
                "\":\" + str(os.getpid()), flush=True)')"
            )
            portal = Portal("", [sys.executable, "-u", "-c", script], root, None)
            runner = web.AppRunner(portal.app())
            await runner.setup()
            self.addAsyncCleanup(runner.cleanup)
            site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=tls)
            await site.start()
            url = f"https://127.0.0.1:{runner.addresses[0][1]}"
            portal.origin = url
            headers = {"Origin": url}

            async def until(socket, needle):
                result = b""
                async with asyncio.timeout(5):
                    while needle not in result:
                        result += await socket.receive_bytes()
                return result

            async with ClientSession(cookie_jar=CookieJar(unsafe=True)) as client:
                async with client.get(url, ssl=trust) as response:
                    self.assertIn("一次性配对码", await response.text())
                with self.assertRaises(WSServerHandshakeError) as unauth:
                    await client.ws_connect(url + "/terminal", ssl=trust, headers=headers)
                self.assertEqual(unauth.exception.status, 401)
                code = portal.pairing
                async with client.post(url + "/pair", ssl=trust, data={"code": code}) as response:
                    self.assertEqual(response.status, 403)
                async with client.post(url + "/pair", ssl=trust, data={"code": code},
                                       headers=headers) as response:
                    self.assertEqual(response.status, 200)
                    self.assertTrue(response.cookies["nn-terminal"]["secure"])
                    self.assertTrue(response.cookies["nn-terminal"]["httponly"])
                async with client.post(url + "/pair", ssl=trust, data={"code": code},
                                       headers=headers) as response:
                    self.assertEqual(response.status, 401)
                async with client.ws_connect(url + "/terminal", ssl=trust, headers=headers) as ws:
                    await until(ws, b"agent-ready")
                    await ws.send_bytes(b"first\r")
                    reply = await until(ws, b"agent-ack:first:")
                    while not reply.endswith(b"\n"):
                        reply += await ws.receive_bytes(timeout=5)
                    first = reply.split(b"agent-ack:first:", 1)[1].splitlines()[0]
                async with asyncio.timeout(3):
                    while portal.socket is not None:
                        await asyncio.sleep(0.02)
                async with client.ws_connect(url + "/terminal", ssl=trust, headers=headers) as ws:
                    await until(ws, b"agent-ack:first:" + first)
                    await ws.send_bytes(b"second\r")
                    await until(ws, b"agent-ack:second:" + first)
                    revocation = asyncio.create_task(portal.revoke())
                    try:
                        # ConPTY can have output already queued before revocation.
                        async with asyncio.timeout(5):
                            while (frame := await ws.receive()).type == WSMsgType.BINARY:
                                pass
                        self.assertIn(frame.type, (WSMsgType.CLOSE, WSMsgType.CLOSED))
                    finally:
                        await asyncio.wait_for(revocation, 5)
                    self.assertIsNone(portal.cookie)
                    self.assertIsNone(portal.terminal)
                with self.assertRaises(WSServerHandshakeError) as revoked:
                    await client.ws_connect(url + "/terminal", ssl=trust, headers=headers)
                self.assertEqual(revoked.exception.status, 401)
