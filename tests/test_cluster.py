import asyncio
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp import ClientResponseError, web

from cluster import CLUSTER, Cluster, digest, validate_metrics
from file_transfer import FileReceiver, send_file
from relay_worker import WORKER, application as worker_app
from signal_server import Rendezvous, Session
import test_integration as base

ADMIN = "a" * 64


def metrics(cpu=10, used=20, cores=2):
    return {"cpu_percent": cpu, "cpu_count": cores, "memory_total": 100,
            "memory_used": used, "disk_total": 1000, "disk_used": 100, "network_bps": 0}


def node(url, capacity=2):
    return {"name": "node", "url": url, "capacity": capacity, "high": 85, "low": 70,
            "bandwidth_mbps": 100, "drain": False, "token_hash": digest("b" * 64)}


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.now = 1
        self.state = Rendezvous({}, lambda: self.now)
        self.cluster = Cluster(self.state, ADMIN, clock=lambda: self.now)
        self.cluster.nodes = {"a": node("https://a.example"), "b": node("https://b.example")}
        for key in self.cluster.nodes:
            self.cluster.report(key, metrics())

    def session(self, sid):
        self.state.sessions[sid] = Session("alice", "bob", self.now + 100, "approved", "file")

    def test_overload_new_placement_and_existing_affinity(self):
        self.session("one")
        first = self.cluster.route("alice", "one")
        self.cluster.report("a", metrics(cpu=90))
        self.session("two")
        second = self.cluster.route("alice", "two")
        self.assertIn("a.example", first["url"])
        self.assertIn("b.example", second["url"])
        self.assertEqual(self.cluster.route("alice", "one"), first)
        self.assertEqual(self.cluster.route("bob", "one")["url"], first["url"])
        self.assertNotEqual(self.cluster.route("bob", "one")["ticket"], first["ticket"])
        for _ in range(2):
            self.cluster.report("a", metrics(cpu=60))
            self.assertEqual(self.cluster.status("a"), "overloaded")
        self.cluster.report("a", metrics(cpu=60))
        self.assertEqual(self.cluster.status("a"), "ready")

    def test_capacity_staleness_drain_and_fail_closed(self):
        self.cluster.nodes["a"].update(high=90, low=90)
        for value, expected in ((90, "overloaded"), (89.99, "ready"), (91, "overloaded"), (81, "ready")):
            self.cluster.report("a", metrics(cpu=value))
            self.assertEqual(self.cluster.status("a"), expected)
        self.cluster.nodes["a"]["drain"] = True
        self.cluster.nodes["b"]["capacity"] = 1
        self.session("one")
        self.cluster.route("alice", "one")
        self.session("two")
        with self.assertRaises(web.HTTPServiceUnavailable):
            self.cluster.route("alice", "two")
        del self.state.sessions["one"]
        self.cluster.route("alice", "two")
        self.now += 16
        self.session("three")
        with self.assertRaises(web.HTTPServiceUnavailable):
            self.cluster.route("alice", "three")
        self.assertEqual(self.cluster.snapshot()["online"], 0)

    def test_weighted_totals_and_reject_invalid_metrics(self):
        self.cluster.report("a", metrics(cpu=20, cores=1))
        self.cluster.report("b", metrics(cpu=80, cores=3))
        totals = self.cluster.snapshot()["totals"]
        self.assertEqual(totals["cpu_percent"], 65)
        self.assertEqual(totals["memory_total"], 200)
        for key, value in (("cpu_percent", float("nan")), ("cpu_count", True),
                           ("memory_used", 101), ("network_bps", -1)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_metrics({**metrics(), key: value})

    def test_history_does_not_fill_offline_gaps(self):
        with patch("cluster.time.time", side_effect=[100, 103, 116]):
            self.cluster.sample_history()
            self.cluster.sample_history()
            self.now += 16
            self.cluster.sample_history()
        samples = self.cluster.snapshot()["history"]
        self.assertEqual([p["cpu"] for p in samples], [10, None])
        self.assertEqual([p["online"] for p in samples], [2, 0])

    def test_node_registry_persists_without_live_assignments(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nodes.json"
            cluster = Cluster(self.state, ADMIN, path)
            cluster.persist({"a" * 32: node("https://a.example")})
            loaded = Cluster(self.state, ADMIN, path)
            self.assertEqual(loaded.nodes, cluster.nodes)
            self.assertEqual(loaded.status("a" * 32), "offline")
            self.assertNotIn("b" * 64, path.read_text())


class ClusterIntegrationTests(unittest.IsolatedAsyncioTestCase):
    app_options = {"admin_token": ADMIN}
    asyncSetUp = base.IntegrationTest.asyncSetUp

    async def admin(self, data=None, token=ADMIN):
        url = self.alice.config["signaling"] + "/admin-api/nodes"
        async with self.alice.http.request("POST" if data is not None else "GET", url,
                                           json=data, ssl=self.alice.signaling_tls,
                                           headers={"Authorization": "Bearer " + token}) as response:
            response.raise_for_status()
            return await response.json()

    async def start_worker(self, name):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.setblocking(False)
        self.stack.callback(sock.close)
        address = "https://127.0.0.1:" + str(sock.getsockname()[1])
        credentials = await self.admin({"name": name, "url": address, "capacity": 8})
        config = {"coordinator": self.alice.config["signaling"],
                  "coordinator_ca": self.alice.config["signal_ca"], "node_id": credentials["id"]}
        app = worker_app(config, credentials["token"])
        app[WORKER].sampler.sample = lambda: metrics()
        runner = web.AppRunner(app)
        await runner.setup()
        self.stack.push_async_callback(runner.cleanup)
        await web.SockSite(runner, sock, ssl_context=self.site._ssl_context).start()
        async with asyncio.timeout(5):
            while not app[WORKER].ready:
                await asyncio.sleep(.01)
        return credentials, app[WORKER]

    async def test_admin_auth_validation_and_no_secret_disclosure(self):
        with self.assertRaises(ClientResponseError) as denied:
            await self.admin(token="wrong")
        self.assertEqual(denied.exception.status, 401)
        for url in ("http://127.0.0.1", "https://user:password@example.com", "https://example.com/path"):
            with self.assertRaises(ClientResponseError) as invalid:
                await self.admin({"name": "bad", "url": url})
            self.assertEqual(invalid.exception.status, 400)
        credentials = await self.admin({"name": "<script>test</script>", "url": "https://a.example"})
        snapshot = await self.admin()
        self.assertNotIn(credentials["token"], json.dumps(snapshot))
        self.assertNotIn("token_hash", json.dumps(snapshot))
        self.assertEqual(snapshot["nodes"][0]["status"], "offline")
        await self.admin({"id": credentials["id"], "drain": True, "capacity": 4})
        self.assertTrue((await self.admin())["nodes"][0]["drain"])

    async def test_atomic_import_and_one_time_credentials(self):
        first = {"name":"first","url":"https://first.example"}
        with self.assertRaises(ClientResponseError) as bad:
            await self.admin({"import":[first, first]})
        self.assertEqual(bad.exception.status, 400)
        self.assertEqual((await self.admin())["total"], 0)
        result = await self.admin({"import":[first,{"name":"second","url":"https://second.example"}]})
        snap = await self.admin()
        self.assertEqual((snap["total"],snap["online"]), (2,0))
        self.assertEqual(len({n["token"] for n in result["registered"]}),2)
        for n in result["registered"]:
            self.assertNotIn(n["token"],json.dumps(snap))

    async def test_two_node_relay_transfer_and_capacity_switch(self):
        first, worker1 = await self.start_worker("first")
        second, worker2 = await self.start_worker("second")
        cluster = self.site._runner.app[CLUSTER]
        # Explicitly overload the first node; prevent its background sample undoing this.
        worker1.sampler.sample = lambda: metrics(cpu=95)
        cluster.report(first["id"], metrics(cpu=95))
        for peer in (self.alice, self.bob):
            peer.config.update(relay=True, cluster=True)
        receiver = FileReceiver(self.bob, lambda _: None)
        port = await receiver.start("127.0.0.1", 0)
        self.stack.push_async_callback(receiver.close)
        await self.bob.call("heartbeat", port=port)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        source = root / "source"
        source.mkdir()
        path = source / "payload.bin"
        marker = b"private-cluster-marker-" * 1000
        path.write_bytes(marker)
        sid = (await self.alice.call("request", target="bob", purpose="file"))["id"]
        await receiver.allow(sid, "alice", root)
        original = self.alice.call
        with socket.socket() as blocked:
            blocked.bind(("127.0.0.1", 0))
            async def unreachable(action, **fields):
                value = await original(action, **fields)
                if action == "endpoint":
                    value["port"] = blocked.getsockname()[1]
                return value
            self.alice.call = unreachable
            records = []
            send_bytes = web.WebSocketResponse.send_bytes
            async def record(ws, data, *args, **kwargs):
                records.append(data)
                await send_bytes(ws, data, *args, **kwargs)
            with patch.object(web.WebSocketResponse, "send_bytes", record):
                await asyncio.wait_for(send_file(self.alice, "bob", sid, path), 15)
        self.assertEqual(cluster.assignments[sid]["node"], second["id"])
        self.assertEqual((root / path.name).read_bytes(), marker)
        self.assertTrue(records)
        self.assertNotIn(marker[:100], b"".join(records))
        async with asyncio.timeout(5):
            while not cluster.assignments[sid]["closed"]:
                await asyncio.sleep(.02)
        self.assertEqual(cluster.reserved(second["id"]), 0)
        self.assertFalse(worker1.pairs)

    async def test_ticket_bound_to_node_and_revocation_closes_worker_pair(self):
        first, worker = await self.start_worker("first")
        second, _ = await self.start_worker("second")
        cluster = self.site._runner.app[CLUSTER]
        cluster.nodes[second["id"]]["drain"] = True
        await self.bob.call("heartbeat", port=9443)
        sid = (await self.alice.call("request", target="bob", purpose="file"))["id"]
        await self.bob.call("approve", id=sid)
        route = await self.alice.call("relay_endpoint", id=sid)
        with self.assertRaises(web.HTTPNotFound):
            cluster.session_for_node(second["id"], sid)
        async with self.alice.http.ws_connect(route["url"], ssl=self.alice.signaling_tls,
                                              headers={"Authorization": "Bearer " + route["ticket"]}) as channel:
            self.assertIn(sid, worker.pairs)
            del cluster.state.sessions[sid]
            await worker.tick()
            self.assertNotEqual((await channel.receive(timeout=5)).type.name, "BINARY")
            self.assertFalse(worker.pairs)
