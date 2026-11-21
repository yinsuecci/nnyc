"""Receive-only phone offer -> locally authorized desktop video and bounded input."""
import asyncio
import re
import time
from collections import deque
from fractions import Fraction

from desktop_stream import MAX_SIZE, input_event


def verify_code(sdp):
    values = set(re.findall(r"^a=fingerprint:sha-256 ((?:[0-9a-f]{2}:){31}[0-9a-f]{2})\r?$",
                            sdp, re.MULTILINE | re.IGNORECASE))
    if len(values) != 1:
        raise ValueError("Missing or ambiguous phone fingerprint")
    return values.pop().replace(":", "").upper()[:16]


async def host_rtc(client, sid, backend, capture, fps, enable_input, confirm, stun_url=None,
                   review_artwork=None, privacy_verified=lambda: False):
    from aiortc import (RTCConfiguration, RTCIceServer, RTCPeerConnection,
                        RTCSessionDescription, VideoStreamTrack)
    from av import VideoFrame
    ended = asyncio.Event()
    pc = RTCPeerConnection(RTCConfiguration(
        iceServers=[RTCIceServer(urls=stun_url)] if stun_url else []))
    arrivals = deque()
    channel_used = False
    controls = None
    artwork_used = False
    reviewing = None

    class DesktopTrack(VideoStreamTrack):
        async def recv(self):
            if ended.is_set():
                raise RuntimeError("Sharing stopped")
            await asyncio.sleep(1/fps)
            def frame():
                image = capture()
                image.thumbnail(MAX_SIZE)
                return VideoFrame.from_image(image.convert("RGB"))
            result = await asyncio.to_thread(frame)
            result.pts = int(time.monotonic() * 90000)
            result.time_base = Fraction(1, 90000)
            return result

    @pc.on("connectionstatechange")
    def connection_state():
        if pc.connectionState in ("failed", "closed"):
            ended.set()

    @pc.on("datachannel")
    def data_channel(channel):
        nonlocal channel_used, artwork_used, reviewing, controls
        if channel.label == "nn-artwork":
            if artwork_used or not enable_input or review_artwork is None or privacy_verified():
                channel.close()
                return
            from rtc_artwork import IncomingArtwork, decode_artwork
            artwork_used = True
            receiver = IncomingArtwork()
            async def review(data):
                try:
                    image = await asyncio.to_thread(decode_artwork, data)
                    async with asyncio.timeout(60):
                        saved = await review_artwork(image)
                    reply = "saved" if saved else "declined"
                except Exception:
                    reply = "rejected"
                if not ended.is_set() and channel.readyState == "open":
                    channel.send(reply)
            @channel.on("message")
            def receive_artwork(msg):
                nonlocal reviewing
                if ended.is_set() or (reviewing and not reviewing.done()):
                    channel.close()
                    return
                try:
                    reply, data = receiver.feed(msg)
                    if data is not None:
                        backend.release()
                        reviewing = asyncio.create_task(review(data))
                    channel.send(reply)
                except (ValueError, TypeError):
                    channel.close()
            @channel.on("close")
            def cancel_review():
                if reviewing:
                    reviewing.cancel()
            return
        if channel.label != "nn-input" or channel_used:
            channel.close()
            return
        channel_used = True
        controls = channel
        channel.send("input:allowed" if enable_input else "input:view")
        if privacy_verified():
            channel.send("privacy:verified")

        @channel.on("message")
        def message(payload):
            if not enable_input or ended.is_set() or (reviewing and not reviewing.done()):
                return
            now = time.monotonic()
            while arrivals and arrivals[0] <= now - 1:
                arrivals.popleft()
            try:
                if not isinstance(payload, str) or len(arrivals) >= 240:
                    raise ValueError()
                arrivals.append(now)
                backend.handle(*input_event(payload))
            except (ValueError, OSError):
                ended.set()

        channel.on("close", ended.set)

    try:
        await client.call("approve", id=sid)
        async with asyncio.timeout(65):
            while not (offer := (await client.call("rtc_read", id=sid))["sdp"]):
                await asyncio.sleep(0.5)
            if not await confirm(verify_code(offer)):
                raise ValueError("未确认手机身份")
            await pc.setRemoteDescription(RTCSessionDescription(offer, "offer"))
            pc.addTrack(DesktopTrack())
            await pc.setLocalDescription(await pc.createAnswer())
            await client.call("rtc_publish", id=sid, sdp=pc.localDescription.sdp)
            await client.call("claim", id=sid, purpose="desktop")
        while not ended.is_set():
            await client.call("active", id=sid)
            if controls and controls.readyState == "open" and privacy_verified():
                controls.send("privacy:verified")
            try:
                await asyncio.wait_for(ended.wait(), 1)
            except TimeoutError:
                pass
    finally:
        ended.set()
        if reviewing:
            reviewing.cancel()
            await asyncio.gather(reviewing, return_exceptions=True)
        try:
            await pc.close()
        finally:
            backend.release()
