"""Test real Dart HTTPS handshakes against disposable CAs and a loopback service."""
import argparse
import http.server
import json
import ssl
import subprocess
import tempfile
import threading
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from provision_demo import certificate
from tools.export_phone_directory import export


class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == "/healthz" else 404)
        self.end_headers()
        self.wfile.write(b'{"ready":true}')

    def log_message(self, *_):
        pass


def check(dart):
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        for name in ("server", "other"):
            (base / name).mkdir()
            # The leaf is also the explicit trust anchor in this isolated test.
            certificate(base / name, "localhost")
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(base / "server/cert.pem", base / "server/key.pem")
        with http.server.ThreadingHTTPServer(("127.0.0.1", 0), Health) as server:
            server.socket = tls.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                paths = []
                for label, ca in (("match", "server/cert.pem"), ("wrong", "other/cert.pem"),
                                  ("default", None)):
                    profile = base / "windows.json"
                    profile.write_text(json.dumps(dict(signaling=f"https://127.0.0.1:{server.server_port}",
                        signal_ca=ca, token="never export", key="never export")), encoding="utf8")
                    public = export(profile, "office", "Office")
                    paths.append(base / (label + ".json"))
                    payload = json.dumps(public)
                    if "never export" in payload:
                        raise AssertionError("Exporter included private fields")
                    paths[-1].write_text(payload, encoding="utf8")
                subprocess.run([dart, str(ROOT / "mobile/tool/check_private_tls.dart"), *paths],
                               check=True, timeout=60)
            finally:
                server.shutdown()
                thread.join()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dart")
    check(parser.parse_args().dart)
