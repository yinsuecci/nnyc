"""Single-host pilot: private control plane and separately routed client listeners."""
import asyncio
import json
import os
import ssl
from pathlib import Path

from aiohttp import web
from cluster import CLUSTER, admin_page
from signal_server import STATE, application
from relay_worker import WORKER, application as worker_application


async def main():
    runtime = Path("/run/nn")
    config_path = runtime / "config.json"
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))
        admin, worker_token = config["admin_token"], config["worker_token"]
        if not all(isinstance(v, str) and len(v) >= 32 for v in (admin, worker_token)):
            raise ValueError("Invalid credentials in config.json")
        if admin == worker_token:
            raise ValueError("Use separate administrator and worker credentials")
    else:
        # Pre-config.json pilot installations only.
        admin = (runtime / "admin").read_text().strip()
        worker_token = (runtime / "worker-token").read_text().strip()
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_3
    tls.load_cert_chain(runtime / "cert.pem", runtime / "key.pem")
    registry = runtime / "registry.json"
    control = application(json.loads(registry.read_text()), registry_file=registry,
                          admin_token=admin,
                          cluster_file="/var/lib/nn-remote/nodes.json")
    worker = worker_application(json.loads((runtime / "worker.json").read_text()),
                                worker_token)
    public = web.Application(client_max_size=4096)
    if os.environ.get("NN_PUBLIC_DASHBOARD") == "1":
        cluster = control[CLUSTER]
        public.router.add_get("/admin", admin_page)
        public.router.add_get("/admin/{asset}", admin_page)
        public.router.add_get("/admin-api/nodes", cluster.admin_api)
        public.router.add_post("/admin-api/nodes", cluster.admin_api)
        public.router.add_post("/cluster/{node}/{action}", cluster.node_api)
    public.router.add_post("/v1/{action}", control[STATE].dispatch)
    public.router.add_get("/v1/relay/{sid}", worker[WORKER].attach)
    async def health(request):
        return web.json_response({"ready": worker[WORKER].ready},
                                 status=200 if worker[WORKER].ready else 503)
    public.router.add_get("/healthz", health)
    runners = []
    try:
        # The worker only needs outbound connections to the coordinator.
        for app, host, port in ((control, "127.0.0.1", 19446),
                                (worker, None, None),
                                (public, os.environ["NN_LISTEN_IP"], 19445)):
            runner = web.AppRunner(app, access_log=None)
            await runner.setup()
            runners.append(runner)
            if host:
                await web.TCPSite(runner, host, port, ssl_context=tls).start()
        await asyncio.Event().wait()
    finally:
        for runner in reversed(runners):
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
