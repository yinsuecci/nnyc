"""Validate a provisioned desktop profile before asking for device credentials."""
import json
from pathlib import Path
from urllib.parse import urlsplit


def read_profile(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file() or path.stat().st_size > 262144:
        raise ValueError("请选择有效的设备配置文件")
    profile = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(profile, dict) or not isinstance(profile.get("signaling"), str):
        raise ValueError("配置缺少信令地址")
    origin = urlsplit(profile["signaling"])
    if (origin.scheme != "https" or not origin.hostname or origin.username is not None
            or origin.password is not None or origin.query or origin.fragment or origin.path not in ("", "/")):
        raise ValueError("信令地址必须是 HTTPS 根地址，不得包含凭据")
    if origin.port == 0:
        raise ValueError("信令地址端口无效")
    peers = profile.get("peers")
    if not isinstance(peers, dict) or not 1 <= len(peers) <= 40:
        raise ValueError("必须预配 1 至 40 台可信设备")

    def credential(value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("证书或密钥路径无效")
        resource = (path.parent / value).resolve(strict=True)
        if not resource.is_file():
            raise ValueError("证书或密钥不是文件")
        return str(resource)

    profile["cert"] = credential(profile.get("cert"))
    profile["key"] = credential(profile.get("key"))
    profile["signal_ca"] = credential(profile["signal_ca"]) if profile.get("signal_ca") else None
    profile["peers"] = {key: credential(value) for key, value in peers.items()}
    profile["settings_path"] = path.with_suffix(".settings.json")
    return profile


def device_token(value):
    token = value.strip()
    if len(token) != 64 or any(c not in "0123456789abcdefABCDEF" for c in token):
        raise ValueError("设备令牌须为 64 位十六进制字符")
    return token
