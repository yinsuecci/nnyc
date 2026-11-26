import hashlib
from unittest import IsolatedAsyncioTestCase

from aiohttp.test_utils import TestClient, TestServer

from signal_server import application


class MobilePairTest(IsolatedAsyncioTestCase):
    async def test_code_to_authorized_session(self) -> None:
        host = "a" * 64
        now = 1000.0
        app = application({hashlib.sha256(host.encode()).hexdigest(): "pc"}, clock=lambda: now)
        async with TestClient(TestServer(app)) as client:
            auth = {"Authorization": f"Bearer {host}"}
            async def issue() -> str:
                await client.post("/v1/heartbeat", headers=auth, json={"port": 9999, "desktop": True, "rtc": True})
                res = await client.post("/v1/pair_code", headers=auth, json={})
                self.assertEqual(res.status, 200)
                return (await res.json())["code"]

            old = await issue()
            now += 121
            redeem = "/v1/mobile/redeem"
            self.assertEqual((await client.post(redeem, json={"target": "pc", "code": old})).status, 401)
            code = await issue()
            body = {"target": "pc", "code": code}
            res = await client.post(redeem, json=body)
            self.assertEqual(res.status, 200)
            session = await res.json()
            viewer = {"Authorization": "Bearer " + session["session_key"]}
            sid = {"id": session["id"]}
            self.assertEqual((await client.post(redeem, json=body)).status, 401)
            self.assertEqual((await client.post("/v1/inbox", headers=viewer, json={})).status, 401)
            inbox = await (await client.post("/v1/inbox", headers=auth, json={})).json()
            self.assertTrue(inbox["pending"][0]["paired"])
            offer = {**sid, "sdp": "v=0\r\n"}
            publish = "/v1/mobile/rtc_publish"
            self.assertEqual((await client.post(publish, headers=viewer, json=offer)).status, 409)
            self.assertEqual((await client.post("/v1/approve", headers=auth, json=sid)).status, 200)
            self.assertEqual((await client.post(publish, headers=viewer, json=offer)).status, 200)
            self.assertEqual((await client.post("/v1/claim", headers=auth, json={**sid, "purpose": "desktop"})).status, 200)
            self.assertEqual((await client.post("/v1/mobile/active", headers=viewer, json=sid)).status, 200)
            self.assertEqual((await client.post("/v1/mobile/cancel", headers=viewer, json=sid)).status, 200)
            self.assertEqual((await client.post("/v1/mobile/active", headers=viewer, json=sid)).status, 401)
