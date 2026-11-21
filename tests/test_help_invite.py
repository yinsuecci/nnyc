import unittest
import test_integration as base


class HelpInvitationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = base.IntegrationTest.asyncSetUp
    rejected = base.IntegrationTest.rejected

    async def test_offer_requires_separate_desktop_consent(self):
        await self.bob.call("heartbeat", port=9445)
        await self.rejected(422, self.alice, "request", target="bob", purpose="help")
        await self.alice.call("heartbeat", port=9444, desktop=True)
        help_id = (await self.alice.call("request", target="bob", purpose="help"))["id"]
        await self.rejected(403, self.alice, "offer_help", id=help_id)
        await self.rejected(403, self.bob, "approve", id=help_id)
        await self.rejected(403, self.alice, "endpoint", id=help_id)
        new_id = (await self.bob.call("offer_help", id=help_id))["id"]
        self.assertNotEqual(new_id, help_id)
        await self.rejected(404, self.bob, "offer_help", id=help_id)
        pending = (await self.alice.call("inbox"))["pending"]
        self.assertEqual(pending, [{"id":new_id,"source":"bob","purpose":"desktop"}])
        await self.rejected(409, self.bob, "endpoint", id=new_id)
        await self.alice.call("deny", id=new_id)
        await self.rejected(404, self.bob, "endpoint", id=new_id)
