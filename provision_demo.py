"""Generate isolated, short-lived test identities. Never deploy these demo files."""
import argparse
import hashlib
import ipaddress
import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def certificate(directory, name):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.now(UTC)
    names = [x509.DNSName(name)]
    if name == "localhost":
        names.append(x509.IPAddress(ipaddress.ip_address("127.0.0.1")))
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=2))
            .not_valid_after(now + timedelta(days=7))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName(names), critical=False)
            .sign(key, hashes.SHA256()))
    (directory / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private_write(directory / "key.pem", key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))


def private_write(path, data):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as file:
        file.write(data)


def provision(root):
    root.mkdir(mode=0o700)
    directories = {name: root / name for name in ("server", "alice", "bob")}
    for name, directory in directories.items():
        directory.mkdir(mode=0o700)
        certificate(directory, "localhost" if name == "server" else "nn-peer")
    registry = {}
    configs = {}
    for name, other, port in (("alice", "bob", 9444), ("bob", "alice", 9445)):
        directory = directories[name]
        token = secrets.token_hex(32)
        private_write(directory / "token", token.encode())
        registry[hashlib.sha256(token.encode()).hexdigest()] = name
        (directory / "peer.pem").write_bytes((directories[other] / "cert.pem").read_bytes())
        (directory / "signal-ca.pem").write_bytes((directories["server"] / "cert.pem").read_bytes())
        configs[name] = {
            "signaling": "https://127.0.0.1:8443", "signal_ca": str(directory / "signal-ca.pem"),
            "cert": str(directory / "cert.pem"), "key": str(directory / "key.pem"),
            "bind": "127.0.0.1", "port": port, "peers": {other: str(directory / "peer.pem")},
        }
    server = directories["server"]
    configs["server"] = {"host": "127.0.0.1", "port": 8443, "devices": registry,
                         "cert": str(server / "cert.pem"), "key": str(server / "key.pem")}
    for name, config in configs.items():
        (directories[name] / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    return configs


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("new_directory", type=Path)
    provision(parser.parse_args().new_directory.resolve())
