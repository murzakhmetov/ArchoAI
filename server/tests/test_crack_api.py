"""Tests of the crack detection API (scripts/scripts/main.py) that the web UI (web-deepcrack) uses.

Starts the real API with uvicorn on a free port of 127.0.0.1, with the real DeepCrack model, and
sends it what the web page sends. Run from the repository root:

    scripts/scripts/venv/bin/python -m unittest discover -s server/tests -p test_crack_api.py -v
"""
import base64
import pathlib
import socket
import struct
import subprocess
import sys
import time
import unittest

import requests

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts" / "scripts"
PHOTO = SCRIPTS / "camera_test_photos" / "camera_index_2.jpg"


def png_size(data):
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    return struct.unpack(">II", data[16:24])


class CrackApiTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            import torch  # noqa: F401  (the API needs it; skip on a machine without the AI stack)
            import uvicorn  # noqa: F401
        except ImportError as exc:
            raise unittest.SkipTest(f"AI dependencies missing: {exc}")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        cls.base = f"http://127.0.0.1:{port}"
        cls.server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=SCRIPTS, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                if requests.get(cls.base + "/", timeout=2).status_code == 200:
                    return
            except requests.RequestException:
                time.sleep(0.5)
        cls.tearDownClass()
        raise RuntimeError("the API did not start within 120 s")

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        try:
            cls.server.wait(timeout=20)
        except subprocess.TimeoutExpired:
            cls.server.kill()

    def predict(self, dim, unit, data, name="photo.jpg", mime="image/jpeg"):
        return requests.post(f"{self.base}/predict/{dim}/{unit}", files={"file": (name, data, mime)}, timeout=180)

    def test_photo_gives_the_marked_image_and_six_maps(self):
        response = self.predict("10-5.5", "cm", PHOTO.read_bytes())
        self.assertEqual(response.status_code, 200, response.text[:300])
        images = response.json()
        self.assertEqual(len(images), 7, "marked image + fused + side1..side5")
        for encoded in images:
            self.assertEqual(png_size(base64.b64decode(encoded)), (256, 256))

    def test_units_the_web_page_accepts(self):
        for unit in ("mm", "%D1%81%D0%BC", "px"):  # "см" (Cyrillic) is mapped to cm
            with self.subTest(unit=unit):
                self.assertEqual(self.predict("1-1", unit, PHOTO.read_bytes()).status_code, 200)

    def test_bad_input_is_answered_with_a_clear_error(self):
        cases = [("1-1", "m", b"not an image", 415), ("1-1", "m", b"", 415),
                 ("abc", "m", PHOTO.read_bytes(), 422), ("0-5", "m", PHOTO.read_bytes(), 422),
                 ("1-1", "a_b", PHOTO.read_bytes(), 422)]
        for dim, unit, data, status in cases:
            with self.subTest(dim=dim, unit=unit, size=len(data)):
                response = self.predict(dim, unit, data)
                self.assertEqual(response.status_code, status, response.text[:200])
                self.assertIn("detail", response.json())

    def test_the_web_page_origin_may_call_the_api(self):
        response = requests.options(f"{self.base}/predict/1-1/m", timeout=10, headers={
            "Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("access-control-allow-origin"), "*")


if __name__ == "__main__":
    unittest.main()
