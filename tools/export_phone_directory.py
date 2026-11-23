"""Export public iPhone connection metadata from an operator-trusted Windows profile."""
import argparse
import base64
import json
from pathlib import Path
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import serialization


def export(profile_path, windows_id, name):
    profile = json.loads(profile_path.read_text(encoding="utf-8-sig"))
    origin = urlsplit(profile["signaling"])
    if (origin.scheme != "https" or not origin.hostname or origin.username is not None
            or origin.fragment or origin.query or origin.path not in ("", "/")):
        raise ValueError("Expected a credential-free HTTPS origin")
    if not windows_id.strip() or len(windows_id) > 128:
        raise ValueError("Invalid Windows device ID")
    data = {"signaling": origin.geturl(), "peers": {windows_id: ""},
            "device_names": {windows_id: name or windows_id}}
    if ca := profile.get("signal_ca"):
        certificates = x509.load_pem_x509_certificates((profile_path.parent / ca).read_bytes())
        if len(certificates) != 1:
            raise ValueError("Expected exactly one trusted public CA certificate")
        data["signal_ca_der"] = base64.b64encode(
            certificates[0].public_bytes(serialization.Encoding.DER)).decode("ascii")
    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("windows_profile", type=Path)
    parser.add_argument("windows_device_id")
    parser.add_argument("output", type=Path)
    parser.add_argument("--name")
    args = parser.parse_args()
    payload = export(args.windows_profile, args.windows_device_id, args.name)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
