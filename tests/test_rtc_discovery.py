import asyncio
import socket
import struct
import unittest

from stun_binding import BindingServer, COOKIE, HEADER, binding_reply
import test_integration as base


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_udp_observed_endpoint(self):
        loop = asyncio.get_running_loop()
        server, _ = await loop.create_datagram_endpoint(BindingServer, local_addr=("127.0.0.1", 0))
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.setblocking(False)
                sock.bind(("127.0.0.1", 0))
                tid = bytes(range(12))
                await loop.sock_sendto(sock, HEADER.pack(1, 0, COOKIE, tid), server.get_extra_info("sockname"))
                data, _ = await asyncio.wait_for(loop.sock_recvfrom(sock, 64), 2)
                self.assertEqual(HEADER.unpack_from(data), (0x101, 12, COOKIE, tid))
                attr, length, family, port, ip = struct.unpack_from("!HHHHI", data, 20)
                self.assertEqual((attr, length, family, port ^ (COOKIE >> 16), ip ^ COOKIE),
                                 (32, 8, 1, sock.getsockname()[1], 0x7f000001))
        finally:
            server.close()

    def test_no_reply_to_invalid_or_relay_requests(self):
        for data in [b"", HEADER.pack(3, 0, COOKIE, bytes(12)),
                     HEADER.pack(1, 4, COOKIE, bytes(12)) + bytes.fromhex("80280010")]:
            self.assertIsNone(binding_reply(data, ("127.0.0.1", 1234)))


class RTCNegotiationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = base.IntegrationTest.asyncSetUp
    rejected = base.IntegrationTest.rejected

    async def test_consent_roles_retries_and_revocation(self):
        await self.bob.call("heartbeat", port=9445, desktop=True, rtc=True)
        sid = (await self.alice.call("request", target="bob", purpose="desktop", transport="rtc"))["id"]
        sdp = "v=0\r\n" + "a=test\r\n" * 600
        await self.rejected(409, self.alice, "rtc_publish", id=sid, sdp=sdp)
        await self.bob.call("approve", id=sid)
        await self.rejected(409, self.bob, "rtc_publish", id=sid, sdp=sdp)
        await self.alice.call("rtc_publish", id=sid, sdp=sdp)
        await self.alice.call("rtc_publish", id=sid, sdp=sdp)
        await self.rejected(409, self.alice, "rtc_publish", id=sid, sdp=sdp+"x")
        self.assertEqual((await self.bob.call("rtc_read", id=sid))["sdp"], sdp)
        await self.bob.call("rtc_publish", id=sid, sdp=sdp+"answer")
        await self.bob.call("claim", id=sid, purpose="desktop")
        self.assertEqual((await self.alice.call("rtc_read", id=sid))["sdp"], sdp+"answer")
        await self.bob.call("cancel", id=sid)
        await self.rejected(404, self.alice, "rtc_read", id=sid)
