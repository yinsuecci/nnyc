"""Remote opaque relay worker. Control-plane failures close active relays."""
import argparse
import asyncio
import json
import logging
import os
import ssl
from types import SimpleNamespace

from aiohttp import ClientError, ClientSession, ClientTimeout, web

from cluster import bearer, valid_url
from relay import Relay
from server_metrics import Sampler

LOG = logging.getLogger(__name__)
WORKER = web.AppKey("worker", object)


class Worker(Relay):
    def __init__(self, config, token):
        super().__init__(SimpleNamespace(sessions={}))
        self.config = config
        self.token = token
        self.base = valid_url(config["coordinator"]) + "/cluster/" + config["node_id"]
        self.tls = ssl.create_default_context(cafile=config.get("coordinator_ca"))
        self.tls.minimum_version = ssl.TLSVersion.TLSv1_3
        self.sampler = Sampler(config.get("disk_path", "/"))
        self.ready = False

    async def call(self, action, **body):
        async with self.http.post(self.base + "/" + action, json=body, ssl=self.tls,
                                  allow_redirects=False,
                                  headers={"Authorization": "Bearer " + self.token}) as response:
            if response.status != 200:
                response.raise_for_status()
                raise ConnectionError("Unexpected coordinator response")
            return await response.json()

    async def authorize(self, request):
        if not self.ready:
            raise web.HTTPServiceUnavailable()
        sid = request.match_info["sid"]
        ticket = bearer(request)
        if len(sid) != 64 or len(ticket) != 64:
            raise web.HTTPUnauthorized()
        try:
            data = await self.call("authorize", sid=sid, ticket=ticket)
        except (ClientError, OSError, TimeoutError):
            raise web.HTTPForbidden() from None
        session = SimpleNamespace(source=data["source"], target=data["target"], state=data["state"])
        self.state.sessions[sid] = session
        return data["device"], sid, session

    async def close_pair(self, sid, pair):
        if pair.closed:
            return
        await super().close_pair(sid, pair)
        self.state.sessions.pop(sid, None)
        try:
            await self.call("close", sid=sid)
        except (ClientError, OSError, TimeoutError):
            pass

    async def tick(self):
        try:
            metrics = await asyncio.to_thread(self.sampler.sample)
            reply = await self.call("report", **metrics)
            self.capacity = reply["capacity"]
            self.ready = True
        except (ClientError, OSError, ValueError, TimeoutError):
            self.ready = False
        async def check(sid):
            try:
                data = await self.call("status", sid=sid)
                self.state.sessions[sid] = SimpleNamespace(**data)
            except (ClientError, OSError, TimeoutError):
                self.state.sessions.pop(sid, None)
        await asyncio.gather(*(check(sid) for sid in list(self.pairs)))
        await self.sweep()


async def lifecycle(app):
    worker = app[WORKER]
    async with ClientSession(timeout=ClientTimeout(total=2)) as worker.http:
        async def poll():
            while True:
                try:
                    await worker.tick()
                except Exception:
                    worker.ready = False
                    LOG.exception("Worker control-plane poll failed")
                    await worker.shutdown(app)
                await asyncio.sleep(3)
        task = asyncio.create_task(poll())
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await worker.shutdown(app)


def application(config, token):
    if not isinstance(token, str) or len(token) != 64:
        raise ValueError("NN_NODE_TOKEN must be a provisioned 64-character token")
    app = web.Application(client_max_size=4096)
    worker = app[WORKER] = Worker(config, token)
    app.router.add_get("/v1/relay/{sid}", worker.attach)
    async def health(request):
        return web.json_response({"ready": worker.ready}, status=200 if worker.ready else 503)
    app.router.add_get("/healthz", health)
    app.cleanup_ctx.append(lifecycle)
    return app


def main():
    parser = argparse.ArgumentParser(description="nn remote relay node")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    with open(args.config, encoding="utf-8") as file:
        config = json.load(file)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_3
    tls.load_cert_chain(config["cert"], config["key"])
    logging.basicConfig(level=logging.INFO)
    web.run_app(application(config, os.environ.get("NN_NODE_TOKEN")),
                host=config.get("host", "127.0.0.1"), port=config.get("port", 9445),
                ssl_context=tls, access_log=None, handler_cancellation=True)


if __name__ == "__main__":
    main()
