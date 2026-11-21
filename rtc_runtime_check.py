"""Verify native transport libraries in the frozen client without external services."""
import asyncio


async def verify():
    import av
    from aiortc import RTCConfiguration, RTCPeerConnection
    from rtc_host import verify_code
    av.Codec("vp8", "r")
    av.Codec("libvpx", "w")
    config = RTCConfiguration(iceServers=[])
    source, target = RTCPeerConnection(config), RTCPeerConnection(config)
    try:
        incoming = asyncio.get_running_loop().create_future()
        @target.on("datachannel")
        def connected(channel):
            @channel.on("message")
            def echo(data):
                if data == "nn-check":
                    channel.send("confirmed")
        outbound = source.createDataChannel("self-test")
        outbound.on("open", lambda: outbound.send("nn-check"))
        outbound.on("message", lambda data: incoming.set_result(data) if not incoming.done() else None)
        async with asyncio.timeout(15):
            await source.setLocalDescription(await source.createOffer())
            verify_code(source.localDescription.sdp)
            await target.setRemoteDescription(source.localDescription)
            await target.setLocalDescription(await target.createAnswer())
            await source.setRemoteDescription(target.localDescription)
            if await incoming != "confirmed":
                raise RuntimeError("WebRTC self-test failed")
    finally:
        await asyncio.gather(source.close(), target.close())
