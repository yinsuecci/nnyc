"""Optional transport for opaque, inner TLS records."""
import asyncio
from dataclasses import dataclass, field

from aiohttp import WSMsgType, web

CHUNK = 64 * 1024
RATE = 12 * 1024 * 1024


@dataclass
class Pair:
    sockets: dict = field(default_factory=dict)
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    initializing: bool = False
    closed: bool = False


class Relay:
    def __init__(self, state, capacity=8):
        self.state = state
        self.pairs = {}
        self.capacity = capacity

    async def authorize(self, request):
        now = self.state.clock()
        self.state.rate(self.state.global_requests, now, 128)
        device = self.state.authenticate(request)
        self.state.rate(self.state.device_requests[device], now, 8)
        self.state.expire(now)
        sid = request.match_info["sid"]
        session = self.state.sessions.get(sid)
        if (session is None or device not in (session.source, session.target)
                or session.purpose not in ("desktop", "file")):
            raise web.HTTPNotFound()
        if session.state != "approved":
            raise web.HTTPConflict()
        return device, sid, session

    async def attach(self, request):
        device, sid, session = await self.authorize(request)
        pair = self.pairs.get(sid)
        if pair is None:
            if len(self.pairs) >= self.capacity:
                raise web.HTTPTooManyRequests()
            pair = self.pairs[sid] = Pair()
        if device in pair.sockets or pair.closed:
            raise web.HTTPConflict()

        socket = web.WebSocketResponse(max_msg_size=CHUNK, compress=False, timeout=2, heartbeat=15)
        pair.sockets[device] = socket
        try:
            await socket.prepare(request)
            if (not pair.initializing and len(pair.sockets) == 2
                    and all(ws.prepared for ws in pair.sockets.values())):
                pair.initializing = True
                await asyncio.gather(*(ws.send_str("ready") for ws in pair.sockets.values()))
                pair.ready.set()
            await asyncio.wait_for(pair.ready.wait(), 30)
            if pair.closed:
                return socket
            remote = pair.sockets[session.target if device == session.source else session.source]
            loop = asyncio.get_running_loop()
            limit = loop.time()
            async for frame in socket:
                if frame.type != WSMsgType.BINARY or sid not in self.state.sessions:
                    break
                limit = max(limit, loop.time()) + len(frame.data) / RATE
                await asyncio.sleep(max(0, limit - loop.time() - 0.05))
                async with asyncio.timeout(4):
                    await remote.send_bytes(frame.data)
        except (OSError, TimeoutError):
            pass
        finally:
            await self.close_pair(sid, pair)
        return socket

    async def close_pair(self, sid, pair):
        if pair.closed:
            return
        pair.closed = True
        pair.ready.set()
        try:
            await asyncio.gather(*(ws.close() for ws in pair.sockets.values() if ws.prepared),
                                 return_exceptions=True)
        finally:
            if self.pairs.get(sid) is pair:
                del self.pairs[sid]

    async def sweep(self):
        expired = [self.close_pair(sid, pair) for sid, pair in list(self.pairs.items())
                   if sid not in self.state.sessions or (not pair.ready.is_set()
                       and self.state.sessions[sid].state == "claimed")]
        await asyncio.gather(*expired)

    async def shutdown(self, _):
        await asyncio.gather(*(self.close_pair(sid, pair) for sid, pair in list(self.pairs.items())))
