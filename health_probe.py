"""Verify signal readiness through the configured TLS identity."""
import os
import ssl
from urllib.request import urlopen

url = os.environ.get("NN_HEALTH_URL", "https://127.0.0.1:8443/healthz")
if not url.startswith("https://"):
    raise SystemExit("health probe requires HTTPS")
tls = ssl.create_default_context(cafile=os.environ.get("NN_HEALTH_CA") or None)
tls.minimum_version = ssl.TLSVersion.TLSv1_3
with urlopen(url, context=tls, timeout=3) as response:
    if response.status != 200:
        raise SystemExit(1)
