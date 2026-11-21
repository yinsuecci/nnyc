"""Install the actual simulator app, cold-launch twice and retain a screenshot."""
import json
import plistlib
import subprocess
import time
from pathlib import Path


def sim(*args):
    return subprocess.check_output(["xcrun", "simctl", *args], text=True).strip()


def main():
    app = Path("build/ios/iphonesimulator/Runner.app").resolve(strict=True)
    with (app / "Info.plist").open("rb") as stream:
        bundle = plistlib.load(stream)["CFBundleIdentifier"]
    runtimes = json.loads(sim("list", "devices", "available", "--json"))["devices"]
    phones = [(tuple(map(int, runtime.split("iOS-")[-1].split("-"))), device)
              for runtime, devices in runtimes.items() if "SimRuntime.iOS-" in runtime
              for device in devices if device["isAvailable"] and device["name"].startswith("iPhone")]
    if not phones:
        raise SystemExit("No iPhone simulator runtime available")
    _, phone = max(phones, key=lambda item: item[0])
    udid = phone["udid"]
    booted_here = phone["state"] == "Shutdown"
    if booted_here:
        sim("boot", udid)
    try:
        sim("bootstatus", udid, "-b")
        sim("install", udid, str(app))
        for attempt in range(2):
            response = sim("launch", udid, bundle)
            pid = int(response.rsplit(":", 1)[1])
            time.sleep(4)
            subprocess.run(["ps", "-p", str(pid)], check=True)
            if attempt == 1:
                sim("io", udid, "screenshot", "build/ios/iphone-launch.png")
            sim("terminate", udid, bundle)
        sim("uninstall", udid, bundle)
    finally:
        if booted_here:
            sim("shutdown", udid)


if __name__ == "__main__":
    main()
