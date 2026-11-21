"""Interactive, one-session reference client for nn远程."""
import argparse
import asyncio
import ipaddress
import json
import os
import ssl
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from aiohttp import ClientError, ClientSession, ClientTimeout

MAX_MESSAGE = 65536


def peer_tls(server, cert, key, trusted_peer):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER if server else ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.verify_mode = ssl.CERT_REQUIRED
    if isinstance(trusted_peer, dict):
        for path in trusted_peer.values():
            context.load_verify_locations(cafile=path)
    else:
        context.load_verify_locations(cafile=trusted_peer)
    context.load_cert_chain(cert, key)
    return context


def verify_peer(writer, cert_path):
    expected = ssl.PEM_cert_to_DER_cert(Path(cert_path).read_text(encoding="ascii"))
    if writer.get_extra_info("ssl_object").getpeercert(binary_form=True) != expected:
        raise ValueError("对端设备证书不匹配")


async def read_message(reader):
    size = int.from_bytes(await reader.readexactly(4), "big")
    if size > MAX_MESSAGE:
        raise ValueError("消息过长")
    return await reader.readexactly(size)


async def write_message(writer, data):
    if len(data) > MAX_MESSAGE:
        raise ValueError("消息过长")
    writer.write(len(data).to_bytes(4, "big") + data)
    await writer.drain()


async def close_stream(writer):
    writer.close()
    with suppress(OSError, TimeoutError):
        async with asyncio.timeout(2):
            await writer.wait_closed()


class Peer:
    def __init__(self, config, token):
        self.config = config
        self.token = token
        self.signaling_tls = ssl.create_default_context(cafile=config["signal_ca"])
        self.signaling_tls.minimum_version = ssl.TLSVersion.TLSv1_3
        if not config["signaling"].startswith("https://"):
            raise ValueError("信令必须使用 HTTPS")

    async def __aenter__(self):
        self.http = ClientSession(timeout=ClientTimeout(total=4))
        return self

    async def __aexit__(self, *exc):
        try:
            await self.call("leave")
        except Exception:
            pass
        finally:
            await self.http.close()

    async def call(self, action, **fields):
        async with self.http.post(
            f"{self.config['signaling'].rstrip('/')}/v1/{action}",
            json=fields, ssl=self.signaling_tls, allow_redirects=False,
            headers={"Authorization": f"Bearer {self.token}"},
        ) as response:
            if response.status != 200:
                response.raise_for_status()
                raise RuntimeError("信令返回了非预期响应")
            return await response.json()

    def tls(self, server, peer):
        return peer_tls(server, self.config["cert"], self.config["key"],
                        self.config["peers"][peer])


@asynccontextmanager
async def peer_connection(client, target, sid):
    endpoint = await client.call("endpoint", id=sid)
    tls = client.tls(False, target)
    try:
        async with asyncio.timeout(4):
            reader, writer = await asyncio.open_connection(
                str(ipaddress.ip_address(endpoint["ip"])), endpoint["port"],
                ssl=tls, server_hostname="nn-peer", ssl_handshake_timeout=3,
            )
    except ssl.SSLError:
        raise
    except (OSError, TimeoutError):
        if not client.config.get("relay"):
            raise
        from relay_client import relay_connection

        async with relay_connection(client, sid, tls) as (reader, writer):
            verify_peer(writer, client.config["peers"][target])
            yield reader, writer
    else:
        try:
            verify_peer(writer, client.config["peers"][target])
            yield reader, writer
        finally:
            await close_stream(writer)


class AuthorizationEnded(ConnectionError):
    """The signaling service no longer authorizes an active session."""


@asynccontextmanager
async def active_session(client, sid, writer):
    async def watch():
        while True:
            await asyncio.sleep(2)
            try:
                async with asyncio.timeout(2):
                    result = await client.call("active", id=sid)
                if result.get("active") is not True:
                    raise AuthorizationEnded()
            except (ClientError, TimeoutError, ValueError, RuntimeError, AuthorizationEnded):
                writer.transport.abort()
                return

    monitor = asyncio.create_task(watch())
    try:
        yield
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)


async def exchange(client, sid, target, message):
    endpoint = await client.call("endpoint", id=sid)
    ip = str(ipaddress.ip_address(endpoint["ip"]))
    async with asyncio.timeout(10):
        reader, writer = await asyncio.open_connection(
            ip, endpoint["port"], ssl=client.tls(False, target),
            server_hostname="nn-peer", ssl_handshake_timeout=4,
        )
        try:
            verify_peer(writer, client.config["peers"][target])
            await write_message(writer, bytes.fromhex(sid))
            if await read_message(reader) != b"ok":
                raise ValueError("会话未获授权")
            await write_message(writer, message)
            return await read_message(reader)
        finally:
            await close_stream(writer)


async def listener(client, source, bind, port):
    accepted = asyncio.get_running_loop().create_future()
    connections = set()
    allowed = None

    async def receive(reader, writer):
        task = asyncio.current_task()
        connections.add(task)
        try:
            async with asyncio.timeout(8):
                verify_peer(writer, client.config["peers"][source])
                sid = (await read_message(reader)).hex()
                if allowed is None or sid != allowed or accepted.done():
                    return
                await client.call("claim", id=sid)
                await write_message(writer, b"ok")
                payload = await read_message(reader)
                await write_message(writer, payload)
                accepted.set_result(payload)
        except (OSError, ValueError, EOFError, ClientError):
            pass
        finally:
            await close_stream(writer)
            connections.discard(task)

    server = await asyncio.start_server(receive, bind, port, ssl=client.tls(True, source),
                                        ssl_handshake_timeout=4, backlog=16)

    def allow(sid):
        nonlocal allowed
        allowed = sid

    async def stop():
        server.close()
        await server.wait_closed()
        tasks = list(connections)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    return server.sockets[0].getsockname()[1], allow, accepted, stop


async def cli(args):
    from aiohttp import ClientResponseError

    with open(args.config, encoding="utf-8") as file:
        config = json.load(file)
    port = config.get("port", 9443)
    async with Peer(config, os.environ["NN_DEVICE_TOKEN"]) as client:
        if args.mode == "send":
            await client.call("heartbeat", port=port)
            sid = (await client.call("request", target=args.peer))["id"]
            async with asyncio.timeout(15):
                while True:
                    try:
                        reply = await exchange(client, sid, args.peer, args.message.encode())
                        print("收到对端回传", repr(reply.decode(errors="replace")))
                        return
                    except ClientResponseError as exc:
                        if exc.status != 409:
                            raise
                        await asyncio.sleep(0.4)
        else:
            port, allow, accepted, stop = await listener(
                client, args.peer, config.get("bind", "127.0.0.1"), port,
            )
            try:
                while True:
                    await client.call("heartbeat", port=port)
                    requests = (await client.call("inbox"))["pending"]
                    invite = next((item for item in requests if item["source"] == args.peer and item.get("purpose", "message") == "message"), None)
                    if invite:
                        break
                    await asyncio.sleep(1)
                answer = await asyncio.to_thread(input, f"允许 {args.peer} 连接？输入 yes 确认 ")
                if answer != "yes":
                    await client.call("deny", id=invite["id"])
                    return
                allow(invite["id"])
                await client.call("approve", id=invite["id"])
                payload = await asyncio.wait_for(accepted, 15)
                print("收到对端数据", repr(payload.decode(errors="replace")))
            finally:
                await stop()


def main():
    parser = argparse.ArgumentParser(description="nn远程参考客户端")
    parser.add_argument("--config", required=True)
    parser.add_argument("mode", choices=["send", "receive"])
    parser.add_argument("peer")
    parser.add_argument("message", nargs="?", default="nn远程")
    asyncio.run(cli(parser.parse_args()))


if __name__ == "__main__":
    main()
