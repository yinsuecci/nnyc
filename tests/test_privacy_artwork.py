import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image, PngImagePlugin
from privacy_artwork import PAPER, load, read_photo, render, save


class ArtworkTests(unittest.TestCase):
    def test_authoring_keeps_original_for_future_crops_and_strips_metadata(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "artwork.json"
            image = Image.new("RGB", (200, 100), "red")
            image.paste("blue", (100, 0, 200, 100))
            png = io.BytesIO()
            info = PngImagePlugin.PngInfo()
            info.add_text("Description", "private source metadata")
            image.save(png, "PNG", pnginfo=info)
            clean = read_photo(io.BytesIO(png.getvalue()))
            self.assertFalse(clean.info)
            save(path, clean, "fill", 1, .5, 2)
            restored, values = load(path)
            self.assertEqual(values, ("fill", 1, .5, 2))
            self.assertEqual(restored.size, clean.size)
            self.assertGreater(render(restored, (100, 100), *values).getpixel((50, 50))[2], 240)
            self.assertGreater(render(restored, (100, 100), "fill", 0, .5, 2).getpixel((50, 50))[0], 240)
            self.assertEqual(render(clean, (100, 100), "fit").getpixel((20, 0)), PAPER)
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                save(path, clean, zoom=float("nan"))
            self.assertEqual(path.read_bytes(), before)
            save(path, None)
            self.assertIsNone(load(path)[0])

    def test_transparency_and_invalid_source(self):
        buf = io.BytesIO()
        Image.new("RGBA", (10,10), (0,255,0,0)).save(buf, "PNG")
        self.assertEqual(read_photo(io.BytesIO(buf.getvalue())).getpixel((1,1)), PAPER)
        with self.assertRaises(OSError):
            read_photo(io.BytesIO(b"not an image"))
