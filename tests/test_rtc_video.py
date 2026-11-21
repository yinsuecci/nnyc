import asyncio
import io
import json
import unittest
from unittest.mock import Mock

from PIL import Image
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from rtc_host import host_rtc, verify_code
import test_integration as base


class RtcVideoTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = base.IntegrationTest.asyncSetUp

    async def test_video_input_and_end(self):
        await self.alice.call("leave")
        await self.alice.call("viewer_heartbeat")
        await self.bob.call("heartbeat", port=9445, desktop=True, rtc=True)
        sid = (await self.alice.call("request", target="bob", purpose="desktop", transport="rtc"))["id"]
        viewer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        self.addAsyncCleanup(viewer.close)
        viewer.addTransceiver("video", direction="recvonly")
        channel = viewer.createDataChannel("nn-input")
        art = viewer.createDataChannel("nn-artwork")
        replies = asyncio.Queue()
        art.on("message", replies.put_nowait)
        decision = asyncio.Event()
        async def review(image):
            self.assertEqual(image.size, (120,60))
            await decision.wait()
            return True
        input_ready = asyncio.Event()
        channel.on("message", lambda value: input_ready.set() if value == "input:allowed" else None)
        track = asyncio.get_running_loop().create_future()
        viewer.on("track", track.set_result)
        await viewer.setLocalDescription(await viewer.createOffer())
        offer = viewer.localDescription.sdp
        backend = Mock()
        async def confirm(code):
            return code == verify_code(offer)
        host = asyncio.create_task(host_rtc(self.bob, sid, backend,
            lambda: Image.new("RGB", (96,64), "blue"), 30, True, confirm, review_artwork=review))
        try:
            async with asyncio.timeout(20):
                await asyncio.sleep(0.2)
                await self.alice.call("rtc_publish", id=sid, sdp=offer)
                while True:
                    self.now += 0.3
                    reply = (await self.alice.call("rtc_read", id=sid))["sdp"]
                    if reply:
                        break
                    await asyncio.sleep(0.3)
                await viewer.setRemoteDescription(RTCSessionDescription(reply, "answer"))
                frame = await (await track).recv()
                self.assertEqual((frame.width, frame.height), (96,64))
                await input_ready.wait()
                channel.send('["move",123,456]')
                while not backend.handle.called:
                    await asyncio.sleep(0.02)
                backend.handle.assert_called_with("move", [123,456])
                encoded = io.BytesIO()
                Image.new("RGB", (120,60), "green").save(encoded, "PNG")
                data = encoded.getvalue()
                art.send(json.dumps({"bytes": len(data)}))
                self.assertEqual(await replies.get(), "ready")
                art.send(data)
                self.assertEqual(await replies.get(), "next")
                art.send("finish")
                self.assertEqual(await replies.get(), "review")
                channel.send('["button","left",true]')
                await asyncio.sleep(.15)
                self.assertEqual(backend.handle.call_count, 1)
                self.assertTrue(replies.empty())
                decision.set()
                self.assertEqual(await replies.get(), "saved")
                await viewer.close()
                await host
                self.assertEqual(backend.release.call_count, 2)
        finally:
            host.cancel()
            await asyncio.gather(host, return_exceptions=True)
