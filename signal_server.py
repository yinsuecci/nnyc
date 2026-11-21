"""Single-process rendezvous service. Session data stays in memory."""
import argparse
import hashlib
import json
import logging
import os
import secrets
import ssl
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from aiohttp import web

from relay import Relay


LOG = logging.getLogger(__name__)
APPROVAL_TTL = {"message": 15, "file": 30, "desktop": 30}
ACTIVE_TTL = {"file": 900, "desktop": 1800}


def validate_registry(devices):
    if not isinstance(devices, dict) or len(devices) > 5000:
        raise ValueError("devices must be an object with at most 5000 entries")
    for key, device in devices.items():
        if (not isinstance(key, str) or len(key) != 64
                or any(c not in "0123456789abcdef" for c in key)
                or not isinstance(device, str) or not 1 <= len(device) <= 64):
            raise ValueError("invalid SHA-256 digest or device ID")
    if len(set(devices.values())) != len(devices):
        raise ValueError("duplicate device ID")
    return devices


@dataclass
class Presence:
    ip: str
    port: int
    expires: float
    desktop: bool = False


@dataclass
class Session:
    source: str
    target: str
    expires: float
    state: str = "pending"
    purpose: str = "message"


def directory_grants(value):
    if not isinstance(value, dict) or len(value) > 5000:
        raise ValueError("directory_grants must be an object")
    result = {}
    for viewer, peers in value.items():
        if (not isinstance(viewer, str) or not 1 <= len(viewer) <= 64 or
                not isinstance(peers, list) or len(peers) > 40 or
                any(not isinstance(p, str) or not 1 <= len(p) <= 64 for p in peers)):
            raise ValueError("invalid directory grant")
        result[viewer] = frozenset(peers)
    return result


class Rendezvous:
    def __init__(self, devices, clock=time.monotonic, grants=None):
        self.devices = dict(validate_registry(devices))
        self.clock = clock
        self.directory = directory_grants({} if grants is None else grants)
        self.online = {}
        self.sessions = {}
        self.global_requests = deque()
        self.device_requests = {device: deque() for device in devices.values()}
        self.cluster = None

    def update_registry(self, devices):
        updated = dict(validate_registry(devices))
        retained = {device for digest, device in self.devices.items() if updated.get(digest) == device}
        for device in self.device_requests.keys() - retained:
            self.leave(device)
        self.device_requests = {device: self.device_requests[device] if device in retained else deque()
                                for device in updated.values()}
        self.devices = updated

    @staticmethod
    def rate(queue, now, limit):
        while queue and queue[0] <= now - 1:
            queue.popleft()
        if len(queue) >= limit:
            raise web.HTTPTooManyRequests()
        queue.append(now)

    def leave(self, device):
        self.online.pop(device, None)
        for sid, session in list(self.sessions.items()):
            if device in (session.source, session.target):
                del self.sessions[sid]

    def expire(self, now):
        for device, presence in list(self.online.items()):
            if presence.expires <= now:
                self.leave(device)
        for sid, session in list(self.sessions.items()):
            if session.expires <= now:
                del self.sessions[sid]

    def authenticate(self, request):
        scheme, _, token = request.headers.get("Authorization", "").partition(" ")
        if scheme != "Bearer" or len(token) != 64:
            raise web.HTTPUnauthorized()
        digest = hashlib.sha256(token.encode()).hexdigest()
        device = self.devices.get(digest)
        if device is None:
            raise web.HTTPUnauthorized()
        return device

    async def dispatch(self, request):
        self.rate(self.global_requests, self.clock(), 128)
        self.authenticate(request)
        try:
            body = await request.json()
        except (ValueError, UnicodeError):
            raise web.HTTPBadRequest() from None
        if not isinstance(body, dict):
            raise web.HTTPBadRequest()
        now = self.clock()
        device = self.authenticate(request)
        self.rate(self.device_requests[device], now, 8)
        self.expire(now)
        return web.json_response(self.handle(device, request.match_info["action"],
                                             body, request.remote, now))

    def handle(self, device, action, body, ip, now):
        if action == "presence":
            peers = body.get("peers")
            if not isinstance(peers, list) or len(peers) > 40 or any(
                    not isinstance(peer, str) or not 1 <= len(peer) <= 64 for peer in peers):
                raise web.HTTPBadRequest()
            allowed = self.directory.get(device, ())
            return {"devices": {p: p in self.online for p in peers
                                if p in allowed and p != device and p in self.device_requests}}
        if action == "heartbeat":
            port = body.get("port")
            if type(port) is not int or not 1 <= port <= 65535 or not ip:
                raise web.HTTPBadRequest()
            desktop = body.get("desktop", False)
            if type(desktop) is not bool:
                raise web.HTTPBadRequest()
            previous = self.online.get(device)
            if previous and (previous.ip, previous.port) != (ip, port):
                self.leave(device)
            self.online[device] = Presence(ip, port, now + 20, desktop)
            return {"device": device}
        if action == "leave":
            self.leave(device)
            return {}
        if device not in self.online:
            raise web.HTTPConflict()
        if action == "devices":
            peers = body.get("peers")
            if not isinstance(peers, list) or len(peers) > 40 or any(
                not isinstance(peer, str) for peer in peers
            ):
                raise web.HTTPBadRequest()
            return {"devices": {peer: peer in self.online for peer in peers
                                if peer != device and peer in self.device_requests}}
        if action == "inbox":
            return {"pending": [{**{"id": sid, "source": session.source},
                                 **({"purpose": session.purpose} if session.purpose != "message" else {})}
                                for sid, session in self.sessions.items()
                                if session.target == device and session.state == "pending"]}
        if action == "request":
            target = body.get("target")
            if not isinstance(target, str) or target == device or target not in self.online:
                raise web.HTTPNotFound()
            if len(self.sessions) >= 128 or sum(
                device in (s.source, s.target) or target in (s.source, s.target)
                for s in self.sessions.values()
            ) >= 4:
                raise web.HTTPTooManyRequests()
            purpose = body.get("purpose", "message")
            if not isinstance(purpose, str) or purpose not in (*APPROVAL_TTL, "help"):
                raise web.HTTPBadRequest()
            if purpose == "help" and not self.online[device].desktop:
                raise web.HTTPUnprocessableEntity()
            if purpose == "desktop" and not self.online[target].desktop:
                raise web.HTTPUnprocessableEntity()
            sid = secrets.token_hex(32)
            self.sessions[sid] = Session(device, target, now + (15 if purpose == "message" else 60),
                                         purpose=purpose)
            return {"id": sid}
        sid = body.get("id")
        if not isinstance(sid, str):
            raise web.HTTPBadRequest()
        if action == "relay_endpoint" and self.cluster is not None:
            return self.cluster.route(device, sid)
        session = self.sessions.get(sid)
        if session is None or device not in (session.source, session.target):
            raise web.HTTPNotFound()
        if session.purpose == "help":
            if action == "cancel" or (device == session.target and action == "deny"):
                del self.sessions[sid]
                return {}
            if action != "offer_help" or device != session.target:
                raise web.HTTPForbidden()
            if session.source not in self.online or not self.online[session.source].desktop:
                raise web.HTTPConflict()
            replacement = secrets.token_hex(32)
            del self.sessions[sid]
            self.sessions[replacement] = Session(device, session.source, now + 60, purpose="desktop")
            return {"id": replacement}
        if action in ("approve", "deny") and device == session.target:
            if session.state != "pending":
                raise web.HTTPConflict()
            if action == "deny":
                del self.sessions[sid]
            else:
                session.state = "approved"
                session.expires = now + APPROVAL_TTL[session.purpose]
            return {}
        if action == "endpoint" and device == session.source:
            if session.state != "approved":
                raise web.HTTPConflict()
            presence = self.online[session.target]
            return {"ip": presence.ip, "port": presence.port}
        if action == "active":
            if session.state != "claimed" or session.purpose not in ACTIVE_TTL:
                raise web.HTTPConflict()
            return {"active": True}
        if action == "cancel":
            del self.sessions[sid]
            return {}
        if action == "claim" and device == session.target:
            if body.get("purpose", "message") != session.purpose:
                raise web.HTTPConflict()
            if session.state != "approved":
                raise web.HTTPConflict()
            session.state = "claimed"
            if session.purpose in ACTIVE_TTL:
                session.expires = now + ACTIVE_TTL[session.purpose]
            return {}
        raise web.HTTPForbidden()


async def cleanup(app):
    import asyncio

    async def sweep():
        registry_file = app[REGISTRY_FILE]
        previous = None
        while True:
            await asyncio.sleep(1)
            rendezvous = app[STATE]
            rendezvous.expire(rendezvous.clock())
            if app[RELAY] is not None:
                await app[RELAY].sweep()
            if registry_file is None:
                continue
            try:
                stat = registry_file.stat()
                version = stat.st_ino, stat.st_mtime_ns, stat.st_size
                if version != previous:
                    if stat.st_size > 1024 * 1024:
                        raise ValueError("registry too large")
                    rendezvous.update_registry(json.loads(registry_file.read_text(encoding="utf-8")))
                    previous = version
                    LOG.info("Device registry reloaded, %d registered", len(rendezvous.devices))
            except (OSError, ValueError):
                if rendezvous.devices:
                    LOG.error("Device registry unavailable, access suspended")
                rendezvous.update_registry({})
                previous = None

    task = asyncio.create_task(sweep())
    yield
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


STATE = web.AppKey("rendezvous", Rendezvous)
REGISTRY_FILE = web.AppKey("registry_file", Path | None)
RELAY = web.AppKey("relay", Relay | None)


async def health(request):
    if not request.app[STATE].devices:
        return web.json_response({"status": "no_active_registry"}, status=503)
    return web.json_response({"status": "ready"})


def application(devices, clock=time.monotonic, registry_file=None, relay=False,
                admin_token=None, cluster_file=None, grants=None):
    app = web.Application(client_max_size=4096)
    app[STATE] = Rendezvous(devices, clock, grants)
    app[REGISTRY_FILE] = registry_file
    app[RELAY] = Relay(app[STATE]) if relay and not admin_token else None
    if app[RELAY]:
        app.router.add_get("/v1/relay/{sid}", app[RELAY].attach)
        app.on_shutdown.append(app[RELAY].shutdown)
    app.cleanup_ctx.append(cleanup)
    app.router.add_get("/healthz", health)
    app.router.add_post("/v1/{action}", app[STATE].dispatch)
    if admin_token:
        from cluster import install
        install(app, app[STATE], admin_token, cluster_file)
    return app


def main():
    parser = argparse.ArgumentParser(description="nn远程信令")
    parser.add_argument("--config", required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    with open(args.config, encoding="utf-8") as file:
        config = json.load(file)
    logging.basicConfig(level=logging.INFO)
    registry_file = Path(config["registry_file"]) if config.get("registry_file") else None
    try:
        directory_grants(config.get("directory_grants", {}))
        registry = validate_registry(json.loads(registry_file.read_text(encoding="utf-8"))
                                     if registry_file else config["devices"])
    except (OSError, ValueError, KeyError) as error:
        parser.error(f"cannot load device registry: {type(error).__name__}")
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_3
    tls.load_cert_chain(config["cert"], config["key"])
    admin_token = os.environ.get("NN_ADMIN_TOKEN") if config.get("cluster") else None
    if config.get("cluster") and (not admin_token or len(admin_token) < 32 or not config.get("cluster_file")):
        parser.error("cluster requires NN_ADMIN_TOKEN (32+ characters) and cluster_file")
    if args.check:
        print(f"Configuration and TLS loaded, {len(registry)} registered devices")
        return
    web.run_app(application(registry, registry_file=registry_file, relay=config.get("relay") is True,
                            admin_token=admin_token, cluster_file=config.get("cluster_file"),
                            grants=config.get("directory_grants", {})),
                host=config.get("host", "127.0.0.1"),
                port=config.get("port", 8443), ssl_context=tls, access_log=None,
                handler_cancellation=True)


if __name__ == "__main__":
    main()
