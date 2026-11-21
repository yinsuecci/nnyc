import unittest
from aiohttp import ClientResponseError
import test_integration as base


class MobileDirectoryTests(unittest.IsolatedAsyncioTestCase):
    app_options = {"grants": {"bob": ["alice"]}}
    asyncSetUp = base.IntegrationTest.asyncSetUp

    async def test_status_without_publishing_mobile_endpoint(self):
        peers = ["alice", "bob", "not-registered"]
        self.assertEqual(await self.bob.call("presence", peers=peers), {"devices":{"alice":True}})
        self.assertEqual(await self.alice.call("presence", peers=["bob"]), {"devices":{}})
        with self.assertRaises(ClientResponseError) as offline:
            await self.bob.call("inbox")
        self.assertEqual(offline.exception.status, 409)
        self.now += 21
        self.assertEqual(await self.bob.call("presence", peers=peers), {"devices":{"alice":False}})

    async def test_requires_authentication(self):
        self.bob.token = "0" * 64
        with self.assertRaises(ClientResponseError) as rejected:
            await self.bob.call("presence", peers=["alice"])
        self.assertEqual(rejected.exception.status, 401)
