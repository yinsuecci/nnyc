"""Single-coordinator relay placement with authenticated node telemetry."""
import asyncio
from collections import deque
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import secrets
import tempfile
import time
from urllib.parse import urlsplit

from aiohttp import web

from server_metrics import Sampler

CLUSTER = web.AppKey("cluster", object)
PAGE = Path(__file__).with_name("admin_web")
FRESH_SECONDS = 15


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def bearer(request):
    value = request.headers.get("Authorization", "")
    return value[7:] if value.startswith("Bearer ") else ""


def valid_url(value):
    if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 for c in value) or "?" in value or "#" in value:
        raise ValueError("HTTPS URL required")
    url = urlsplit(value)
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.path not in ("", "/") or url.query or url.fragment):
        raise ValueError("HTTPS origin required, without credentials or path")
    _ = url.port
    return value.rstrip("/")


def validate_metrics(data):
    fields = ("cpu_percent", "cpu_count", "memory_total", "memory_used",
              "disk_total", "disk_used", "network_bps")
    if not isinstance(data, dict):
        raise ValueError("invalid metrics")
    for key in fields:
        value = data.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("invalid metric: " + key)
    if (data["cpu_percent"] > 100 or not 1 <= data["cpu_count"] <= 65536
            or data["memory_total"] <= 0 or data["disk_total"] <= 0
            or data["memory_used"] > data["memory_total"]
            or data["disk_used"] > data["disk_total"]):
        raise ValueError("invalid resource totals")
    return {key: data[key] for key in fields}


class Cluster:
    def __init__(self, state, admin_token, path=None, clock=time.monotonic):
        if not isinstance(admin_token, str) or len(admin_token) < 32:
            raise ValueError("NN_ADMIN_TOKEN must contain at least 32 characters")
        self.state, self.clock = state, clock
        self.admin_digest = digest(admin_token)
        self.path = Path(path) if path else None
        self.nodes = {}
        self.runtime = {}
        self.assignments = {}
        self.local = None
        self.usage_history = deque(maxlen=240)
        self.sampler = Sampler()
        self.attempts = []
        if self.path and self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or len(data) > 64:
                raise ValueError("invalid node registry")
            for node_id, node in data.items():
                self.validate_node(node)
                if (not isinstance(node_id, str) or len(node_id) != 32
                        or len(node.get("token_hash", "")) != 64):
                    raise ValueError("invalid node credentials")
            self.nodes = data

    @staticmethod
    def validate_node(node):
        if not isinstance(node, dict) or not isinstance(node.get("name"), str) or not 1 <= len(node["name"]) <= 60:
            raise ValueError("name must contain 1-60 characters")
        valid_url(node.get("url"))
        if type(node.get("capacity")) is not int or not 1 <= node["capacity"] <= 128:
            raise ValueError("capacity must be 1-128")
        if (type(node.get("high")) not in (int, float) or not 50 <= node["high"] <= 95
                or type(node.get("low")) not in (int, float) or not 20 <= node["low"] <= node["high"]):
            raise ValueError("thresholds must satisfy 20 <= low <= high <= 95; high >= 50")
        if type(node.get("drain")) is not bool:
            raise ValueError("drain must be boolean")
        if type(node.get("bandwidth_mbps")) not in (int, float) or not 1 <= node["bandwidth_mbps"] <= 100000:
            raise ValueError("bandwidth_mbps must be 1-100000")

    def persist(self, nodes):
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".nn-nodes-")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as file:
                    json.dump(nodes, file, ensure_ascii=True)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        self.nodes = nodes

    def authenticate_admin(self, request):
        # Global bounded attempts also cover invalid credentials.
        now = self.clock()
        self.attempts = [t for t in self.attempts if t > now - 1]
        if len(self.attempts) >= 20:
            raise web.HTTPTooManyRequests()
        self.attempts.append(now)
        if not hmac.compare_digest(digest(bearer(request)), self.admin_digest):
            raise web.HTTPUnauthorized()

    def authenticate_node(self, request):
        node_id = request.match_info["node"]
        node = self.nodes.get(node_id)
        if node is None or not hmac.compare_digest(digest(bearer(request)), node["token_hash"]):
            raise web.HTTPUnauthorized()
        return node_id

    def prune(self):
        self.state.expire(self.state.clock())
        self.assignments = {sid: value for sid, value in self.assignments.items()
                            if sid in self.state.sessions}

    def reserved(self, node_id):
        return sum(a["node"] == node_id and not a["closed"] for a in self.assignments.values())

    def pressure(self, node, metrics):
        return max(metrics["cpu_percent"], 100 * metrics["memory_used"] / metrics["memory_total"],
                   100 * metrics["disk_used"] / metrics["disk_total"],
                   metrics["network_bps"] * 8 / (node["bandwidth_mbps"] * 1e6) * 100)

    def report(self, node_id, metrics):
        metrics = validate_metrics(metrics)
        node = self.nodes[node_id]
        previous = self.runtime.get(node_id, {})
        pressure = self.pressure(node, metrics)
        # Stop admission immediately at high water; recover after three low samples.
        blocked = previous.get("blocked", False) or pressure >= node["high"]
        low_samples = previous.get("low_samples", 0) + 1 if pressure <= node["low"] else 0
        if node["low"] == node["high"]:
            blocked = pressure >= node["high"]
        elif blocked and low_samples >= 3:
            blocked = False
        self.runtime[node_id] = {"metrics": metrics, "seen": self.clock(),
                                 "blocked": blocked, "low_samples": low_samples}

    def status(self, node_id):
        node, runtime = self.nodes[node_id], self.runtime.get(node_id)
        if runtime is None or self.clock() - runtime["seen"] >= FRESH_SECONDS:
            return "offline"
        if node["drain"]:
            return "draining"
        if runtime["blocked"] or self.pressure(node, runtime["metrics"]) >= node["high"]:
            return "overloaded"
        if self.reserved(node_id) >= node["capacity"]:
            return "full"
        return "ready"

    def route(self, device, sid):
        self.prune()
        session = self.state.sessions.get(sid)
        if session is None or device not in (session.source, session.target):
            raise web.HTTPNotFound()
        if session.state != "approved" or session.purpose not in ("file", "desktop"):
            raise web.HTTPConflict()
        placement = self.assignments.get(sid)
        if placement is None:
            candidates = [node_id for node_id in self.nodes if self.status(node_id) == "ready"]
            if not candidates:
                raise web.HTTPServiceUnavailable(text="No healthy relay capacity; try again later")
            node_id = min(candidates, key=lambda key: (
                max(self.pressure(self.nodes[key], self.runtime[key]["metrics"]) / 100,
                    self.reserved(key) / self.nodes[key]["capacity"]), key))
            placement = {"node": node_id, "closed": False,
                         "tickets": {peer: secrets.token_hex(32) for peer in (session.source, session.target)}}
            self.assignments[sid] = placement
        if placement["closed"] or self.status(placement["node"]) == "offline":
            raise web.HTTPServiceUnavailable(text="Assigned relay unavailable; start a new session")
        return {"url": self.nodes[placement["node"]]["url"] + "/v1/relay/" + sid,
                "ticket": placement["tickets"][device]}

    def session_for_node(self, node_id, sid):
        self.prune()
        placement = self.assignments.get(sid)
        if placement is None or placement["node"] != node_id or placement["closed"]:
            raise web.HTTPNotFound()
        session = self.state.sessions[sid]
        return placement, session

    async def node_api(self, request):
        node_id = self.authenticate_node(request)
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError()
            action = request.match_info["action"]
            if action == "report":
                self.report(node_id, data)
                return web.json_response({"capacity": self.nodes[node_id]["capacity"]})
            sid = data.get("sid")
            if not isinstance(sid, str):
                raise ValueError()
            placement, session = self.session_for_node(node_id, sid)
            if action == "close":
                placement["closed"] = True
                return web.json_response({})
            if action == "authorize":
                ticket = data.get("ticket")
                if not isinstance(ticket, str) or session.state != "approved":
                    raise web.HTTPForbidden()
                device = next((peer for peer, value in placement["tickets"].items()
                               if hmac.compare_digest(ticket.encode(), value.encode())), None)
                if device is None:
                    raise web.HTTPForbidden()
            elif action == "status":
                device = None
            else:
                raise web.HTTPNotFound()
            return web.json_response({"device": device, "source": session.source,
                                      "target": session.target, "state": session.state})
        except (ValueError, TypeError):
            raise web.HTTPBadRequest() from None

    def snapshot(self):
        self.prune()
        nodes, fresh = [], []
        for node_id, node in self.nodes.items():
            runtime = self.runtime.get(node_id)
            status = self.status(node_id)
            metrics = runtime["metrics"] if runtime else None
            if status != "offline":
                fresh.append(metrics)
            nodes.append({"id": node_id, **{k: v for k, v in node.items() if k != "token_hash"},
                          "status": status, "metrics": metrics, "reserved": self.reserved(node_id),
                          "age": round(self.clock() - runtime["seen"], 1) if runtime else None})
        totals = {key: sum(m[key] for m in fresh) for key in (
            "cpu_count", "memory_used", "memory_total", "disk_used", "disk_total", "network_bps")}
        totals["cpu_percent"] = (sum(m["cpu_percent"] * m["cpu_count"] for m in fresh)
                                 / totals["cpu_count"] if fresh else None)
        return {"nodes": nodes, "totals": totals, "online": len(fresh), "total": len(nodes),
                "history": list(self.usage_history),
                "coordinator": self.local, "sessions": len(self.state.sessions),
                "notice": "总占用仅汇总在线中继节点，不含控制节点；同一主机只部署一个采集节点。"}

    async def admin_api(self, request):
        self.authenticate_admin(request)
        if request.method == "GET":
            return web.json_response(self.snapshot(), headers={"Cache-Control": "no-store"})
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError()
            if "import" in data:
                items = data["import"]
                if set(data) != {"import"} or not isinstance(items, list) or not 1 <= len(items) <= 32:
                    raise ValueError("import requires 1-32 server entries")
                credentials = self.register_nodes(items)
                return web.json_response({"registered": credentials}, headers={"Cache-Control":"no-store"})
            node_id = data.get("id")
            nodes = {key: dict(value) for key, value in self.nodes.items()}
            if node_id is not None:
                if not isinstance(node_id, str) or node_id not in nodes:
                    raise web.HTTPNotFound()
                allowed = {"drain", "high", "low", "capacity", "bandwidth_mbps"}
                nodes[node_id].update({k: v for k, v in data.items() if k in allowed})
                self.validate_node(nodes[node_id])
                self.persist(nodes)
                return web.json_response({"id": node_id})
            result = self.register_nodes([data])[0]
            return web.json_response(result, headers={"Cache-Control": "no-store"})
        except (ValueError, TypeError) as error:
            raise web.HTTPBadRequest(text=str(error) or "Invalid request") from None

    def register_nodes(self, items):
        nodes = dict(self.nodes)
        credentials = []
        if len(nodes) + len(items) > 64:
            raise ValueError("maximum 64 nodes")
        for data in items:
            if not isinstance(data, dict) or data.keys() - {"name","url","capacity","high","low","bandwidth_mbps"}:
                raise ValueError("unexpected registration fields; do not import credentials")
            node = {"name": data.get("name"), "url": valid_url(data.get("url")),
                    "capacity": data.get("capacity", 8), "high": data.get("high", 90),
                    "low": data.get("low", data.get("high", 90)), "drain": False,
                    "bandwidth_mbps": data.get("bandwidth_mbps", 100)}
            self.validate_node(node)
            if any(n["url"] == node["url"] for n in nodes.values()):
                raise ValueError("node URL already registered")
            token, node_id = secrets.token_hex(32), secrets.token_hex(16)
            nodes[node_id] = {**node, "token_hash": digest(token)}
            credentials.append({"id": node_id, "name": node["name"], "token": token})
        self.persist(nodes)
        return credentials

    def sample_history(self):
        now = int(time.time())
        if self.usage_history and now - self.usage_history[-1]["time"] < 15:
            return
        sample = self.snapshot()
        self.usage_history.append({"time":now, "cpu":sample["totals"]["cpu_percent"],
                                   "online":sample["online"]})


async def metrics_loop(app):
    cluster = app[CLUSTER]
    async def run():
        while True:
            try:
                cluster.local = await asyncio.to_thread(cluster.sampler.sample)
                cluster.prune()
                cluster.sample_history()
            except OSError:
                cluster.local = None
            await asyncio.sleep(3)
    task = asyncio.create_task(run())
    yield
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def admin_page(request):
    name = request.match_info.get("asset", "index.html")
    if name not in ("index.html", "app.js", "style.css"):
        raise web.HTTPNotFound()
    return web.FileResponse(PAGE / name, headers={
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'",
        "Referrer-Policy": "no-referrer"})


def install(app, state, admin_token, path=None):
    cluster = app[CLUSTER] = Cluster(state, admin_token, path)
    state.cluster = cluster
    app.router.add_get("/admin", admin_page)
    app.router.add_get("/admin/{asset}", admin_page)
    app.router.add_get("/admin-api/nodes", cluster.admin_api)
    app.router.add_post("/admin-api/nodes", cluster.admin_api)
    app.router.add_post("/cluster/{node}/{action}", cluster.node_api)
    app.cleanup_ctx.append(metrics_loop)
    return cluster
