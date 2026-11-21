import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop_profile import device_token, read_profile


class ProfileTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name).resolve(strict=True)
        (self.home / "cert.pem").touch()
        self.path = self.home / "client.json"
        self.data = dict(signaling="https://control.example:8443", cert="cert.pem",
                         key="cert.pem", peers={"office":"cert.pem"})

    def load(self):
        self.path.write_text(json.dumps(self.data), encoding="utf8")
        return read_profile(self.path)

    def test_relative_assets_resolve_against_profile(self):
        profile = self.load()
        self.assertEqual(Path(profile["peers"]["office"]), self.home / "cert.pem")
        self.assertEqual(profile["settings_path"], self.home / "client.settings.json")

    def test_no_credential_bearing_or_insecure_origin(self):
        for url in ["http://signal.example", "https://user:password@signal.example",
                    "https://signal.example/path", "https://signal.example:99999"]:
            self.data["signaling"] = url
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.load()

    def test_missing_file(self):
        self.data["key"] = "missing.pem"
        with self.assertRaises(FileNotFoundError):
            self.load()

    def test_token_errors_do_not_echo_input(self):
        with self.assertRaises(ValueError) as error:
            device_token("example-sensitive-text")
        self.assertNotIn("example-sensitive-text", str(error.exception))
        self.assertEqual(device_token("f"*64+"\n"), "f"*64)

    def test_cancel_without_network(self):
        from desktop import main
        with patch("sys.argv", ["NNRemote"]), patch("desktop.first_run", return_value=None) as gui, \
                patch("desktop.asyncio.run") as run:
            main()
            gui.assert_called_once()
            run.assert_not_called()
