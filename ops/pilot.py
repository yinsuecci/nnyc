"""Manage the private pilot deployment using a Windows user-scoped credential."""
from __future__ import annotations

import argparse
import base64
import ctypes
import getpass
import io
import ipaddress
import json
import tarfile
import zlib
from pathlib import Path
from typing import TypedDict

import paramiko

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / ".local" / "server.json"


class Config(TypedDict):
    host: str
    user: str
    port: int
    password_dpapi: str


def protect(value: str, decrypt: bool = False) -> str:
    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.c_void_p)]

    raw = base64.b64decode(value, validate=True) if decrypt else value.encode()
    buf = ctypes.create_string_buffer(raw)
    src, out = Blob(len(raw), ctypes.cast(buf, ctypes.c_void_p)), Blob()
    dll = ctypes.WinDLL("crypt32", use_last_error=True)
    method = dll.CryptUnprotectData if decrypt else dll.CryptProtectData
    if not method(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(out)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        data = ctypes.string_at(out.data, out.size)
        return data.decode() if decrypt else base64.b64encode(data).decode()
    finally:
        ctypes.memset(out.data, 0, out.size)
        ctypes.windll.kernel32.LocalFree(ctypes.c_void_p(out.data))


REMOTE = r'''
from __future__ import annotations
import http.client, json, os, socket, ssl, subprocess, sys, time, urllib.request, urllib.error

def docker(*args: str, data: bytes | None = None) -> str:
    return subprocess.run(["docker", *args], input=data, stdout=subprocess.PIPE,
                          check=True).stdout.decode().strip()

class Engine(http.client.HTTPConnection):
    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect("/var/run/docker.sock")

name = "nn-remote-pilot"
old = json.loads(docker("inspect", name))[0]
assert old["State"]["Running"] and old["HostConfig"]["NetworkMode"] == "host"
assert old["HostConfig"]["ReadonlyRootfs"] and old["HostConfig"]["RestartPolicy"]["Name"] == "unless-stopped"
mounts = {m["Destination"]:m for m in old["Mounts"]}
assert not mounts["/run/nn"]["RW"] and mounts["/var/lib/nn-remote"]["RW"]
assert old["Config"]["WorkingDir"] == "/app"
stamp = str(int(time.time()))
base = "nn-remote-pilot:pair-base-" + stamp
new = "nn-remote-pilot:pair-" + stamp
bak = name + "-before-pair-" + stamp
docker("tag", old["Image"], base)
docker("build", "--build-arg", "BASE="+base, "-q", "-t", new, "-", data=sys.stdin.buffer.read())
docker("run","--rm","--network","none","--entrypoint","python",new,"-B","-c",
       "from pilot_service import main; from signal_server import application; application({})")
cfg = dict(old["Config"], Image=new, HostConfig=old["HostConfig"])
con = Engine("localhost", timeout=30)
created = False
docker("rename", old["Id"], bak)
try:
    con.request("POST","/containers/create?name="+name,body=json.dumps(cfg),headers={"Content-Type":"application/json"})
    res=con.getresponse()
    if res.status != 201: raise RuntimeError("container creation failed: HTTP "+str(res.status))
    ident=json.loads(res.read())["Id"]
    created=True
    docker("stop", "-t", "12", old["Id"])
    docker("start", ident)
    ctx=ssl.create_default_context(cafile=os.path.join(mounts["/run/nn"]["Source"],"cert.pem"))
    http=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=ctx))
    origin="https://"+sys.argv[1]+":19445"
    for attempt in range(25):
        try:
            req=urllib.request.Request(origin+"/v1/mobile/binding_list",data=b"{}",headers={
                "Content-Type":"application/json","Authorization":"Bearer "+os.urandom(32).hex()})
            with http.open(req,timeout=3) as resp:
                if json.load(resp).get("targets") != []: raise RuntimeError("unexpected directory response")
            with http.open(origin+"/healthz",timeout=3) as resp:
                if not json.load(resp).get("ready"): raise RuntimeError("worker not ready")
            break
        except (OSError,RuntimeError):
            if attempt == 24: raise
            time.sleep(1)
    docker("update","--restart=no",old["Id"])
    print("Updated: binding API and worker healthy. Rollback container:",bak)
except BaseException:
    if created: docker("rm","-f",ident)
    docker("rename",old["Id"],name)
    docker("start",old["Id"])
    print("Restored original container.",file=sys.stderr)
    raise
finally:
    con.close()
'''


def payload_bytes() -> bytes:
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:gz") as tar:
        files = ["pilot_service.py", "signal_server.py", "device_bindings.py", "stun_binding.py"]
        recipe = ("ARG BASE\nFROM ${BASE}\nCOPY " + " ".join(files) + " /app/\n").encode()
        info = tarfile.TarInfo("Dockerfile")
        info.size = len(recipe)
        tar.addfile(info, io.BytesIO(recipe))
        for filename in files:
            tar.add(ROOT / filename, arcname=filename, recursive=False)
    return payload.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["setup", "status", "deploy"])
    parser.add_argument("--host")
    parser.add_argument("--user", default="deploy")
    parser.add_argument("--port", type=int, default=22)
    args = parser.parse_args()
    if args.action == "setup":
        if not args.host:
            parser.error("setup requires --host")
        host = str(ipaddress.IPv4Address(args.host))
        secret = getpass.getpass("SSH password (hidden): ")
        if not secret:
            parser.error("empty password")
        config: Config = {"host":host, "port":args.port, "user":args.user, "password_dpapi":protect(secret)}
        CONFIG.parent.mkdir(exist_ok=True)
        pending = CONFIG.with_suffix(".new")
        pending.write_text(json.dumps(config), encoding="utf-8")
        pending.replace(CONFIG)
        print("Private config saved.")
        return
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    host = str(ipaddress.IPv4Address(config["host"]))
    with paramiko.SSHClient() as client:
        client.load_host_keys(str(Path.home()/".ssh"/"known_hosts"))
        client.connect(host, port=config["port"], username=config["user"],
                       password=protect(config["password_dpapi"], decrypt=True),
                       look_for_keys=False, allow_agent=False, timeout=8, auth_timeout=12)
        if args.action == "status":
            command = "docker inspect --format '{{.State.Status}} {{.Config.Image}}' nn-remote-pilot"
            data = b""
        else:
            encoded = base64.b64encode(zlib.compress(REMOTE.encode())).decode()
            command = f"""python3 -c "import base64,zlib;exec(zlib.decompress(base64.b64decode('{encoded}')))" {host}"""
            data = payload_bytes()
        stdin, stdout, stderr = client.exec_command(command, timeout=240)
        if data:
            stdin.write(data)
            stdin.flush()
        stdin.channel.shutdown_write()
        reply = stdout.read().decode(errors="replace").strip()
        error = stderr.read().decode(errors="replace")
        if stdout.channel.recv_exit_status() != 0:
            raise RuntimeError("Remote operation failed: " + error[-1000:])
        print(reply)


if __name__ == "__main__":
    main()
