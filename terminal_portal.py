"""Direct, explicitly enabled HTTPS terminal with reconnectable PTY."""
import argparse
import asyncio
import hmac
import ipaddress
import os
import secrets
import signal
import ssl
import subprocess
import sys
from collections import deque
from pathlib import Path

from aiohttp import WSMsgType, web

PAGE = Path(__file__).with_name("terminal_web")
HISTORY = 256 * 1024
SESSION_SECONDS = 8 * 60 * 60


class Terminal:
    def __init__(self, loop):
        self.loop = loop
        self.history = bytearray()
        self.subscriber = None
        self.finished = False

    def publish(self, data):
        if self.finished:
            return
        self.history.extend(data)
        if len(self.history) > HISTORY:
            del self.history[:-HISTORY]
        queue = self.subscriber
        if queue is not None:
            if queue.full():
                self.subscriber = None
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(None)
            else:
                queue.put_nowait(data)

    def finish(self):
        if self.finished:
            return
        self.finished = True
        if self.subscriber is not None:
            if self.subscriber.full():
                self.subscriber.get_nowait()
            self.subscriber.put_nowait(None)


class UnixTerminal(Terminal):
    def __init__(self, loop, command, cwd):
        import pty

        super().__init__(loop)
        self.fd, slave = pty.openpty()
        env = dict(os.environ)
        env.pop("NN_DEVICE_TOKEN", None)
        env["TERM"] = "xterm-256color"
        # Acquire the controlling TTY in an isolated child after exec.
        bootstrap = (
            "import fcntl, os, sys, termios;"
            "fcntl.ioctl(0, termios.TIOCSCTTY, 0);"
            "os.execvp(sys.argv[1], sys.argv[1:])"
        )
        try:
            self.process = subprocess.Popen(
                [sys.executable, "-c", bootstrap, *command], cwd=cwd, env=env,
                stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
            )
        except BaseException:
            os.close(self.fd)
            raise
        finally:
            os.close(slave)
        self.resize(80, 24)
        os.set_blocking(self.fd, False)
        loop.add_reader(self.fd, self.read)

    def resize(self, columns, rows):
        import termios

        termios.tcsetwinsize(self.fd, (rows, columns))

    def read(self):
        try:
            data = os.read(self.fd, 8192)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if data:
            self.publish(data)
        else:
            self.loop.remove_reader(self.fd)
            self.finish()

    async def write(self, data):
        while data:
            try:
                data = data[os.write(self.fd, data):]
            except BlockingIOError:
                await asyncio.sleep(0.02)

    async def close(self):
        self.loop.remove_reader(self.fd)
        os.close(self.fd)
        for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(self.process.pid, sig)
            except ProcessLookupError:
                pass
            for _ in range(10):
                if self.process.poll() is not None:
                    self.finish()
                    return
                await asyncio.sleep(0.05)
        self.finish()


class WindowsTerminal(Terminal):
    def __init__(self, loop, command, cwd):
        from winpty import PtyProcess
        from threading import Thread

        super().__init__(loop)
        env = dict(os.environ)
        env.pop("NN_DEVICE_TOKEN", None)
        env["TERM"] = "xterm-256color"
        self.process = PtyProcess.spawn(command, cwd=str(cwd), env=env, dimensions=(24, 80))
        self.thread = Thread(target=self.read, daemon=True)
        self.thread.start()

    def read(self):
        try:
            while data := self.process.read(4096):
                self.loop.call_soon_threadsafe(self.publish, data.encode())
        except (EOFError, OSError):
            pass
        finally:
            self.loop.call_soon_threadsafe(self.finish)

    async def write(self, data):
        await asyncio.to_thread(self.process.write, data.decode("utf-8"))

    def resize(self, columns, rows):
        self.process.setwinsize(rows, columns)

    async def close(self):
        await asyncio.to_thread(self.process.close, True)
        await asyncio.to_thread(self.thread.join, 2)
        self.finish()


class Portal:
    def __init__(self, origin, command, cwd, assets):
        self.origin = origin
        self.command = command
        self.cwd = cwd
        self.assets = assets
        self.pairing = secrets.token_urlsafe(16)
        self.pair_until = asyncio.get_running_loop().time() + 300
        self.cookie = None
        self.session_expires = 0
        self.expiration = None
        self.revoke_lock = asyncio.Lock()
        self.terminal = None
        self.socket = None
        self.attempts = deque()

    def authorized(self, request):
        candidate = request.cookies.get("nn-terminal", "")
        return (self.cookie is not None and candidate.isascii()
                and asyncio.get_running_loop().time() < self.session_expires
                and hmac.compare_digest(candidate, self.cookie))

    def check_origin(self, request):
        if request.headers.get("Origin") != self.origin:
            raise web.HTTPForbidden()

    async def middleware(self, request, handler):
        response = await handler(request)
        response.headers.update({
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; script-src 'self'; "
                                       "style-src 'self' 'unsafe-inline'; connect-src 'self'; "
                                       "frame-ancestors none; form-action 'self'",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        })
        return response

    async def page(self, request):
        names = {"/": "index.html", "/portal.css": "portal.css", "/portal.js": "portal.js"}
        return web.FileResponse(PAGE / names[request.path])

    async def asset(self, request):
        relative = {"xterm.js": "lib/xterm.js", "xterm.css": "css/xterm.css"}
        key = request.match_info["name"]
        if not self.assets or key not in relative or not (self.assets / relative[key]).is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(self.assets / relative[key])

    async def pair(self, request):
        self.check_origin(request)
        now = asyncio.get_running_loop().time()
        while self.attempts and self.attempts[0] <= now - 60:
            self.attempts.popleft()
        if len(self.attempts) >= 10:
            raise web.HTTPTooManyRequests()
        self.attempts.append(now)
        data = await request.post()
        candidate = data.get("code", "")
        if not isinstance(candidate, str) or not candidate.isascii() or self.pairing is None                 or now >= self.pair_until or not hmac.compare_digest(candidate, self.pairing):
            raise web.HTTPUnauthorized()
        factory = WindowsTerminal if os.name == "nt" else UnixTerminal
        self.terminal = factory(asyncio.get_running_loop(), self.command, self.cwd)
        self.pairing = None
        self.cookie = secrets.token_urlsafe(32)
        self.session_expires = now + SESSION_SECONDS
        self.expiration = asyncio.create_task(self.expire_session())
        response = web.json_response({"paired": True})
        response.set_cookie("nn-terminal", self.cookie, secure=True, httponly=True,
                            samesite="Strict", max_age=SESSION_SECONDS)
        return response

    async def ws(self, request):
        self.check_origin(request)
        if not self.authorized(request):
            raise web.HTTPUnauthorized()
        if self.socket is not None or self.terminal.finished:
            raise web.HTTPConflict()
        socket = web.WebSocketResponse(timeout=3, heartbeat=15, max_msg_size=8192, compress=False)
        self.socket = socket
        terminal = self.terminal
        queue = asyncio.Queue(maxsize=64)
        terminal.subscriber = queue
        history = bytes(terminal.history)
        output = None
        try:
            await socket.prepare(request)

            async def pump():
                try:
                    async with asyncio.timeout(5):
                        await socket.send_bytes(history)
                    if terminal.finished:
                        return
                    while (chunk := await queue.get()) is not None:
                        async with asyncio.timeout(5):
                            await socket.send_bytes(chunk)
                finally:
                    await socket.close()

            output = asyncio.create_task(pump())
            async for frame in socket:
                if frame.type == WSMsgType.BINARY:
                    if terminal.finished:
                        break
                    async with asyncio.timeout(5):
                        await terminal.write(frame.data)
                elif frame.type == WSMsgType.TEXT:
                    fields = frame.data.split(" ")
                    if len(fields) == 3 and fields[0] == "size" and all(
                        x.isascii() and x.isdecimal() and len(x) <= 3 for x in fields[1:]
                    ):
                        cols, rows = map(int, fields[1:])
                        if 20 <= cols <= 240 and 5 <= rows <= 100 and not terminal.finished:
                            terminal.resize(cols, rows)
        finally:
            if output is not None:
                output.cancel()
                await asyncio.gather(output, return_exceptions=True)
            terminal.subscriber = None
            self.socket = None
        return socket

    async def expire_session(self):
        await asyncio.sleep(max(0, self.session_expires - asyncio.get_running_loop().time()))
        self.expiration = None
        await self.revoke()

    async def revoke(self):
        async with self.revoke_lock:
            self.cookie = None
            if self.socket is not None:
                await self.socket.close()
            terminal, self.terminal = self.terminal, None
            if terminal is not None:
                await terminal.close()

    async def shutdown(self, app):
        if self.expiration is not None:
            self.expiration.cancel()
            await asyncio.gather(self.expiration, return_exceptions=True)
            self.expiration = None
        await self.revoke()

    def app(self):
        @web.middleware
        async def headers(request, handler):
            return await self.middleware(request, handler)

        app = web.Application(client_max_size=1024, middlewares=[headers])
        for path in ("/", "/portal.css", "/portal.js"):
            app.router.add_get(path, self.page)
        app.router.add_get("/assets/{name}", self.asset)
        app.router.add_post("/pair", self.pair)
        app.router.add_get("/terminal", self.ws)
        app.on_shutdown.append(self.shutdown)
        return app


def main():
    parser = argparse.ArgumentParser(description="nn远程手机终端")
    parser.add_argument("--origin", required=True, help="手机直连的 HTTPS origin，含非默认端口")
    parser.add_argument("--cert", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9446)
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("--assets", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    from urllib.parse import urlsplit
    url = urlsplit(args.origin)
    if url.scheme != "https" or not url.hostname or url.path or url.query or url.fragment             or url.username is not None or (url.port or 443) != args.port:
        parser.error("origin 必须是与监听端口匹配的 HTTPS 地址")
    ipaddress.ip_address(args.bind)
    command = args.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("请明确指定要启动的 Agent 或 shell")
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_3
    tls.load_cert_chain(args.cert, args.key)

    async def create():
        portal = Portal(args.origin, command, args.cwd.resolve(strict=True), args.assets)
        print(f"手机访问 {args.origin}\n一次性配对码（5 分钟有效）：{portal.pairing}", flush=True)
        return portal.app()

    web.run_app(create(), host=args.bind, port=args.port, ssl_context=tls,
                access_log=None, handler_cancellation=True)


if __name__ == "__main__":
    main()
