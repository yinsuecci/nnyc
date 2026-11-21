"""Bridge loopback TCP and an authorized WebSocket without terminating inner TLS."""
import asyncio
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from aiohttp import WSMsgType

from peer import close_stream

CHUNK = 64 * 1024


async def bridge(reader, writer, socket):
    async def upload():
        # Keep each WebSocket message strictly below the receiver's size limit.
        while data := await reader.read(CHUNK // 2):
            await socket.send_bytes(data)

    async def download():
        async for frame in socket:
            if frame.type != WSMsgType.BINARY:
                break
            writer.write(frame.data)
            await writer.drain()

    tasks = [asyncio.create_task(upload()), asyncio.create_task(download())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@asynccontextmanager
async def websocket(client, sid):
    if client.config.get("cluster"):
        route = await client.call("relay_endpoint", id=sid)
        url, token = route["url"], route["ticket"]
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.path != f"/v1/relay/{sid}" or parsed.query or parsed.fragment
                or not isinstance(token, str) or len(token) != 64):
            raise ValueError("Invalid relay route")
    else:
        url = client.config["signaling"].rstrip("/") + f"/v1/relay/{sid}"
        token = client.token
    # Cluster workers receive session-scoped tickets, never long-lived device tokens.
    async with client.http.ws_connect(url, ssl=client.signaling_tls, compress=0, max_msg_size=CHUNK,
                                      headers={"Authorization": f"Bearer {token}"}) as channel:
        yield channel


async def wait_ready(channel):
    frame = await channel.receive(timeout=30)
    if frame.type != WSMsgType.TEXT or frame.data != "ready":
        raise ConnectionError("中继配对失败")


async def accept_relay(client, sid, address):
    async with websocket(client, sid) as channel:
        await wait_ready(channel)
        reader, writer = await asyncio.open_connection(*address)
        try:
            await bridge(reader, writer, channel)
        finally:
            await close_stream(writer)


@asynccontextmanager
async def relay_connection(client, sid, tls):
    tasks = set()
    accepted = False
    async with websocket(client, sid) as channel:
        await wait_ready(channel)
        async def forward(reader, writer):
            nonlocal accepted
            if accepted:
                await close_stream(writer)
                return
            accepted = True
            task = asyncio.current_task()
            tasks.add(task)
            try:
                await bridge(reader, writer, channel)
            except (OSError, RuntimeError):
                pass
            finally:
                await close_stream(writer)
                tasks.discard(task)

        listener = await asyncio.start_server(forward, "127.0.0.1", 0)
        try:
            async with asyncio.timeout(8):
                reader, writer = await asyncio.open_connection(
                    "127.0.0.1", listener.sockets[0].getsockname()[1],
                    ssl=tls, server_hostname="nn-peer", ssl_handshake_timeout=7,
                )
            try:
                yield reader, writer
            finally:
                await close_stream(writer)
        finally:
            listener.close()
            await listener.wait_closed()
            for task in list(tasks):
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
