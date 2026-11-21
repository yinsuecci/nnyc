"""Check an unsigned device build; arm64 alone does not distinguish the simulator."""
import plistlib
import re
import subprocess
import sys
from pathlib import Path


def is_device_binary(load_commands):
    platforms = re.findall(r"^\s*platform\s+(\S+)", load_commands, re.MULTILINE)
    return bool(platforms) and all(value.upper() in {"IOS", "2"} for value in platforms)


def verify(app):
    with (app / "Info.plist").open("rb") as f:
        info = plistlib.load(f)
    if info.get("DTPlatformName") != "iphoneos" or "iPhoneOS" not in info.get("CFBundleSupportedPlatforms", []):
        raise ValueError("Not an iPhoneOS device bundle")
    for key in ("NSLocalNetworkUsageDescription", "NSPhotoLibraryUsageDescription"):
        if not info.get(key):
            raise ValueError(f"Missing device permission description: {key}")
    for binary in [app / info["CFBundleExecutable"], app / "Frameworks/App.framework/App",
                   app / "Frameworks/Flutter.framework/Flutter"]:
        arches = subprocess.check_output(["xcrun", "lipo", "-archs", binary], text=True).split()
        if "arm64" not in arches:
            raise ValueError(f"No arm64 slice: {binary.name}")
        commands = subprocess.check_output(["xcrun", "otool", "-arch", "arm64", "-l", binary], text=True)
        if not is_device_binary(commands):
            raise ValueError(f"No iOS device platform in {binary.name}")
    print("Verified unsigned iPhoneOS arm64 app; signing, installation and hardware tests not covered.")


if __name__ == "__main__":
    verify(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("build/ios/iphoneos/Runner.app"))
