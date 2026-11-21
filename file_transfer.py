"""Consent-bound file transfer over a direct, pinned TLS connection."""
import asyncio
import hashlib
import hmac
import json
import os
import tempfile
import unicodedata
from pathlib import Path

from aiohttp import ClientError

from peer import MAX_MESSAGE, active_session, close_stream, peer_connection, peer_tls, read_message, verify_peer, write_message

MAX_FILE = 2 * 1024**3
FILE_PROTOCOL = b"file-v2"


def valid_name(name):
    return (
        isinstance(name, str) and 0 < len(name.encode("utf-8")) <= 240
        and name not in (".", "..") and name[0] != "."
        and not name.endswith((" ", "."))
        and not any(c in '<>:"/\\|?*' or unicodedata.category(c).startswith("C") for c in name)
        and name.split(".")[0].rstrip().upper() not in {
            "CON", "PRN", "AUX", "NUL",
            *(f"{prefix}{n}" for prefix in ("COM", "LPT") for n in "123456789¹²³"),
        }
    )


async def send_file(client, target, sid, path, progress=None):
    path = Path(path)
    if not valid_name(path.name) or not path.is_file():
        raise ValueError("请选择普通文件，且文件名须兼容 Windows 与 macOS")
    async with asyncio.timeout(900), peer_connection(client, target, sid) as (reader, writer):
        await write_message(writer, bytes.fromhex(sid))
        if await read_message(reader) != FILE_PROTOCOL:
            raise ValueError("对端未授权文件接收或文件协议版本不兼容，请更新两端")
        async with active_session(client, sid, writer):
            with path.open("rb") as source:
                size = os.fstat(source.fileno()).st_size
                if size > MAX_FILE:
                    raise ValueError("单文件最大 2 GiB")
                await write_message(writer, json.dumps({"name": path.name, "size": size}).encode())
                remaining = size
                digest = hashlib.sha256()
                while remaining:
                    chunk = source.read(min(remaining, MAX_MESSAGE))
                    if not chunk:
                        raise ValueError("文件在传输期间被截断")
                    await write_message(writer, chunk)
                    remaining -= len(chunk)
                    digest.update(chunk)
                    if progress:
                        progress(size - remaining, size)
            await write_message(writer, digest.digest())
            if await read_message(reader) != digest.digest():
                raise ValueError("接收端未确认文件完整性")
            return digest.hexdigest()


async def receive_file(reader, writer, directory, authorize):
    data = json.loads(await read_message(reader))
    if not isinstance(data, dict):
        raise ValueError("文件信息无效")
    name, size = data.get("name"), data.get("size")
    if not valid_name(name) or type(size) is not int or not 0 <= size <= MAX_FILE:
        raise ValueError("文件名或大小无效")
    destination = directory / name
    fd, temp = tempfile.mkstemp(dir=directory, prefix=".nn-transfer-")
    try:
        digest = hashlib.sha256()
        with os.fdopen(fd, "wb") as output:
            remaining = size
            while remaining:
                chunk = await read_message(reader)
                if not chunk or len(chunk) > remaining:
                    raise ValueError("文件长度异常")
                output.write(chunk)
                digest.update(chunk)
                remaining -= len(chunk)
            expected = await read_message(reader)
            if not hmac.compare_digest(expected, digest.digest()):
                raise ValueError("文件校验不一致，已取消接收")
            output.flush()
            os.fsync(output.fileno())
        # Atomic publication without replacing an existing file. Requires hard-link support.
        authorization = await authorize()
        if not isinstance(authorization, dict) or authorization.get("active") is not True:
            raise PermissionError("文件会话授权已失效")
        os.link(temp, destination)
        await write_message(writer, digest.digest())
        return destination
    finally:
        os.unlink(temp)


class FileReceiver:
    def __init__(self, client, on_receive=print):
        self.client = client
        self.on_receive = on_receive
        self.approvals = {}
        self.tasks = set()
        self.relays = set()

    async def start(self, bind, port):
        config = self.client.config
        tls = peer_tls(True, config["cert"], config["key"], config["peers"])
        self.server = await asyncio.start_server(
            self.receive, bind, port, ssl=tls, ssl_handshake_timeout=4, backlog=16,
        )
        return self.server.sockets[0].getsockname()[1]

    async def allow(self, sid, source, directory):
        path = Path(directory).resolve(strict=True)
        if not path.is_dir():
            raise ValueError("请选择接收目录")

        async def receive(reader, writer):
            destination = await receive_file(
                reader, writer, path, lambda: self.client.call("active", id=sid),
            )
            self.on_receive(f"收到文件：{destination.name}")

        await self.allow_service(sid, source, "file", receive)

    async def allow_service(self, sid, source, purpose, handler):
        now = asyncio.get_running_loop().time()
        self.approvals = {key: value for key, value in self.approvals.items() if value[2] > now}
        if len(self.approvals) >= 4 or source not in self.client.config["peers"]:
            raise ValueError("接收队列已满或设备尚未配对")
        self.approvals[sid] = (source, purpose, now + 30, handler)
        try:
            await self.client.call("approve", id=sid)
            if self.client.config.get("relay"):
                task = asyncio.create_task(self.start_relay(sid))
                self.relays.add(task)
                task.add_done_callback(self.relays.discard)
        except BaseException:
            self.approvals.pop(sid, None)
            raise

    async def start_relay(self, sid):
        from relay_client import accept_relay

        sock = self.server.sockets[0].getsockname()
        host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(sock[0], sock[0])
        try:
            async with asyncio.timeout(1800):
                await accept_relay(self.client, sid, (host, sock[1]))
        except (ClientError, OSError, TimeoutError):
            pass

    async def receive(self, reader, writer):
        task = asyncio.current_task()
        if len(self.tasks) >= 8:
            await close_stream(writer)
            return
        self.tasks.add(task)
        try:
            async with asyncio.timeout(1800):
                sid = (await asyncio.wait_for(read_message(reader), 5)).hex()
                approval = self.approvals.get(sid)
                if approval is None:
                    return
                source, purpose, expires, handler = approval
                verify_peer(writer, self.client.config["peers"][source])
                self.approvals.pop(sid, None)
                if asyncio.get_running_loop().time() >= expires:
                    return
                await self.client.call("claim", id=sid, purpose=purpose)
                await write_message(writer, FILE_PROTOCOL if purpose == "file" else purpose.encode())
                async with active_session(self.client, sid, writer):
                    await handler(reader, writer)
        except (OSError, EOFError, ValueError, ClientError) as exc:
            self.on_receive(f"接收失败：{type(exc).__name__}")
        finally:
            await close_stream(writer)
            self.tasks.discard(task)

    async def close(self):
        self.server.close()
        await self.server.wait_closed()
        self.approvals.clear()
        tasks = list(self.tasks | self.relays)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
