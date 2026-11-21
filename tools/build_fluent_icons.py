"""Regenerate the bundled Regular icon masks from the pinned Microsoft artwork."""
import io
import json
from pathlib import Path
from urllib.request import urlopen

from PIL import Image, ImageDraw, ImageFont

REV = "6c2c9075cf8952ef48369b2475233b6e9761efed"
BASE = f"https://raw.githubusercontent.com/microsoft/fluentui-system-icons/{REV}/"
OUT = Path(__file__).resolve().parents[1] / "ui_assets" / "fluent"
NAMES = "desktop people folder_arrow_right history settings code copy lock_closed shield_checkmark arrow_swap search laptop".split()


def fetch(path):
    with urlopen(BASE + path, timeout=30) as response:
        return response.read()


def launch_icons(font_data, codepoint):
    root = OUT.parents[1]
    master = Image.new("RGB", (1024, 1024), "#0759ed")
    font = ImageFont.truetype(io.BytesIO(font_data), 744)
    ImageDraw.Draw(master).text((512, 512), chr(codepoint), font=font,
                                fill="white", anchor="mm")
    def write(path, size):
        master.resize((size, size), Image.Resampling.LANCZOS).save(path)
    write(root / "ui_assets" / "nn-remote.png", 128)
    master.save(root / "ui_assets" / "nn-remote.ico",
                sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    android = root / "mobile/android/app/src/main/res"
    for density, size in zip(("mdpi","hdpi","xhdpi","xxhdpi","xxxhdpi"), (48,72,96,144,192)):
        write(android / f"mipmap-{density}/ic_launcher.png", size)
    catalog = root / "mobile/ios/Runner/Assets.xcassets/AppIcon.appiconset"
    for asset in json.loads((catalog / "Contents.json").read_text())["images"]:
        size = float(asset["size"].split("x")[0]) * int(asset["scale"][:-1])
        write(catalog / asset["filename"], round(size))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    mapping = json.loads(fetch("fonts/FluentSystemIcons-Regular.json"))
    font_data = fetch("fonts/FluentSystemIcons-Regular.ttf")
    font = ImageFont.truetype(io.BytesIO(font_data), 96)
    launch_icons(font_data, mapping["ic_fluent_desktop_24_regular"])
    for name in NAMES:
        mask = Image.new("L", (96, 96))
        ImageDraw.Draw(mask).text((48, 48), chr(mapping[f"ic_fluent_{name}_24_regular"]),
                                  font=font, fill=255, anchor="mm")
        if not mask.getbbox():
            raise ValueError(name)
        mask.save(OUT / f"{name}.png")
        mobile = OUT.parents[1] / "mobile" / "assets" / "fluent"
        mobile.mkdir(parents=True, exist_ok=True)
        artwork = Image.new("RGBA", mask.size, "white")
        artwork.putalpha(mask)
        artwork.save(mobile / f"{name}.png")
    (OUT / "LICENSE").write_bytes(fetch("LICENSE"))
    (mobile / "LICENSE").write_bytes((OUT / "LICENSE").read_bytes())


if __name__ == "__main__":
    main()
