"""Create native desktop test artifacts, preserving executable bits and bundle links."""
import argparse
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0"
DESKTOP_ENTRY = """[Desktop Entry]
Type=Application
Name=nn远程
Name[en]=nn Remote
Comment=Connect to a paired device
Exec=/opt/nn-remote/NNRemote
Icon=nn-remote
Terminal=false
Categories=Network;RemoteAccess;
StartupNotify=true
"""


def run(*args):
    subprocess.run(args, check=True, cwd=ROOT)


def verify(executable):
    run(str(executable), "--check-ui-assets")


def linux(dist, work, output):
    arch = subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip()
    package = work / "deb"
    shutil.copytree(dist / "NNRemote", package / "opt/nn-remote", symlinks=True)
    metadata = package / "DEBIAN"
    metadata.mkdir()
    (metadata / "control").write_text(
        f"Package: nn-remote\nVersion: {VERSION}\nArchitecture: {arch}\n"
        "Maintainer: nn Remote\nSection: net\nPriority: optional\n"
        "Depends: libc6, libstdc++6, libx11-6, libxext6, libxrender1, libfontconfig1, fonts-noto-cjk\n"
        "Description: nn Remote paired-device desktop client\n", encoding="utf-8")
    entry = package / "usr/share/applications/nn-remote.desktop"
    entry.parent.mkdir(parents=True)
    entry.write_text(DESKTOP_ENTRY, encoding="utf-8")
    icons = package / "usr/share/icons/hicolor/128x128/apps"
    icons.mkdir(parents=True)
    shutil.copy2(ROOT / "ui_assets/nn-remote.png", icons / "nn-remote.png")
    artifact = output / f"nn-remote_{VERSION}_{arch}.deb"
    run("desktop-file-validate", str(entry))
    run("dpkg-deb", "--build", "--root-owner-group", str(package), str(artifact))
    extracted = work / "unpacked"
    run("dpkg-deb", "--extract", str(artifact), str(extracted))
    verify(extracted / "opt/nn-remote/NNRemote")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        dist = work / "dist"
        args = [sys.executable, "-m", "PyInstaller", "--onedir", "--name", "NNRemote",
                "--distpath", str(dist), "--workpath", str(work / "build"), "--specpath", str(work),
                "--collect-data", "customtkinter"]
        for source, dest in [("ui_assets","ui_assets"),("terminal_web","terminal_web"),
                             ("node_modules/@xterm/xterm","xterm")]:
            args.extend(["--add-data", f"{ROOT / source}:{dest}"])
        if sys.platform == "darwin":
            icon = work / "nn-remote.icns"
            src = ROOT / "mobile/ios/Runner/Assets.xcassets/AppIcon.appiconset/Icon-App-1024x1024@1x.png"
            with Image.open(src) as image:
                image.save(icon)
            args.extend(["--windowed", "--icon", str(icon), "--osx-bundle-identifier", "app.nnremote.desktop"])
        elif sys.platform == "win32":
            args.extend(["--windowed", "--icon", str(ROOT / "ui_assets/nn-remote.ico")])
        run(*args, str(ROOT / "desktop.py"))
        if sys.platform == "darwin":
            app = dist / "NNRemote.app"
            with (app / "Contents/Info.plist").open("rb") as file:
                assert plistlib.load(file)["CFBundleIdentifier"] == "app.nnremote.desktop"
            archive = output / f"NNRemote-{VERSION}-macOS-{platform.machine()}.zip"
            run("ditto", "-c", "-k", "--keepParent", str(app), str(archive))
            extracted = work / "unpacked"
            run("ditto", "-x", "-k", str(archive), str(extracted))
            verify(extracted / "NNRemote.app/Contents/MacOS/NNRemote")
        elif sys.platform == "win32":
            archive = shutil.make_archive(str(output / f"NNRemote-{VERSION}-Windows-{platform.machine()}"),
                                          "zip", dist, "NNRemote")
            extracted = work / "unpacked"
            shutil.unpack_archive(archive, extracted)
            verify(extracted / "NNRemote/NNRemote.exe")
        elif sys.platform.startswith("linux"):
            linux(dist, work, output)
        else:
            raise RuntimeError(f"Unsupported platform: {os.name}")


if __name__ == "__main__":
    main()
