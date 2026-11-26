import hashlib
import tempfile
from pathlib import Path
from unittest import IsolatedAsyncioTestCase

from aiohttp.test_utils import TestClient, TestServer

from signal_server import application


class BoundDeviceJourney(IsolatedAsyncioTestCase):
    async def test_pair_in_either_direction_reconnect_after_restart_and_revoke(self) -> None:
        host = {"Authorization": "Bearer " + "a"*64}
        phone = {"Authorization": "Bearer " + "b"*64}
        stranger = {"Authorization": "Bearer " + "c"*64}
        registry = {hashlib.sha256(("a"*64).encode()).hexdigest(): "office"}
        now = 100.0
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "bindings.sqlite3"
            for issuer in ("host", "phone"):
                async with TestClient(TestServer(application(registry, clock=lambda: now, binding_file=db))) as c:
                    await c.post("/v1/heartbeat", headers=host, json={"port": 1, "desktop": True, "rtc": True})
                    issue, accept = (("/v1/binding_issue", "/v1/mobile/binding_accept") if issuer == "host"
                                     else ("/v1/mobile/binding_issue", "/v1/binding_accept"))
                    first, second = (host, phone) if issuer == "host" else (phone, host)
                    code = (await (await c.post(issue, headers=first, json={})).json())["code"]
                    self.assertEqual((await c.post(accept, headers=second, json={"code": code})).status, 200)
                    self.assertEqual((await c.post(accept, headers=second, json={"code": code})).status, 401)
                now += 3600
                async with TestClient(TestServer(application(registry, clock=lambda: now, binding_file=db))) as c:
                    target = {"target": "office"}
                    url = "/v1/mobile/connect"
                    await c.post("/v1/heartbeat", headers=host, json={"port": 1, "desktop": True, "rtc": True})
                    listing = await (await c.post("/v1/mobile/binding_list", headers=phone, json={})).json()
                    self.assertEqual(listing["targets"], ["office"])
                    self.assertEqual((await c.post(url, headers=stranger, json=target)).status, 403)
                    session = await (await c.post(url, headers=phone, json=target)).json()
                    sid = {"id": session["id"]}
                    viewer = {"Authorization": "Bearer " + session["session_key"]}
                    self.assertEqual((await c.post("/v1/approve", headers=host, json=sid)).status, 200)
                    self.assertEqual((await c.post("/v1/claim", headers=host, json={**sid, "purpose":"desktop"})).status, 200)
                    self.assertEqual((await c.post("/v1/mobile/active", headers=viewer, json=sid)).status, 200)
                    if issuer == "host":
                        phones = await (await c.post("/v1/binding_list", headers=host, json={})).json()
                        response = await c.post("/v1/binding_remove", headers=host, json={"phone": phones["phones"][0]})
                    else:
                        response = await c.post("/v1/mobile/binding_remove", headers=phone, json=target)
                    self.assertEqual(response.status, 200)
                    self.assertEqual((await c.post("/v1/mobile/active", headers=viewer, json=sid)).status, 401)
                    self.assertEqual((await c.post(url, headers=phone, json=target)).status, 403)
