"""Unit tests for camera_view.py (live camera window of the turntable scanner). No window is opened
and no camera is used: a fake capture returns numbered frames.

Run from scripts/scripts:  python -m unittest discover -s tests -v
"""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import camera_view  # noqa: E402


class FakeCapture:
    """Frame n is filled with the value n (mod 256), so a photo shows which frame it came from."""

    def __init__(self):
        self.n = 0

    def read(self):
        self.n += 1
        return True, np.full((48, 64, 3), self.n % 256, np.uint8)


class CameraViewTests(unittest.TestCase):

    def view(self):
        return camera_view.CameraView(FakeCapture(), show=False)

    def test_photo_is_a_jpeg_of_a_frame_read_after_the_request(self):
        view = self.view()
        view.update()  # frame 1, before the request
        requested = time.monotonic()
        threading.Timer(0.05, view.update).start()  # frame 2 arrives later, like the live loop
        jpeg = view.photo(after=requested, timeout=2)
        self.assertEqual(jpeg[:3], b"\xff\xd8\xff")
        pixel = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)[24, 32, 0]
        self.assertAlmostEqual(int(pixel), 2, delta=2, msg="the photo must be the frame read after the request")

    def test_photo_without_a_new_frame_times_out(self):
        view = self.view()
        view.update()
        with self.assertRaisesRegex(RuntimeError, "кадр"):
            view.photo(after=time.monotonic(), timeout=0.2)

    def test_keys_from_the_window(self):
        view = self.view()
        view._keys.extend(["x", " "])
        self.assertEqual(view.poll_key(), "r")
        view._keys.extend(["r", "q", "\x1b"])
        self.assertEqual([view.poll_key(), view.poll_key(), view.poll_key()], ["r", "q", "q"])
        self.assertIsNone(view.poll_key())
        view._keys.append(" ")
        view.drop_keys()
        self.assertIsNone(view.poll_key())

    def test_no_window_without_a_desktop(self):
        view = self.view()
        self.assertFalse(view.window_open)
        view.close()  # harmless

    def test_status_bar_with_russian_text(self):
        frame = np.full((480, 640, 3), 200, np.uint8)
        font = camera_view.CameraView(FakeCapture(), show=False)._get_font(480)
        out = camera_view.draw_text_bar(frame.copy(), "Фото 3/6 (120°)", font)
        self.assertEqual(out.shape, frame.shape)
        bar = out[: max(28, 480 // 16)]
        self.assertLess(bar.mean(), 200, "the bar is darkened")
        self.assertGreater(bar.max(), 230, "the text is drawn in white")
        self.assertTrue((out[100:] == 200).all(), "the rest of the picture is untouched")

    def test_status_bar_without_a_font_falls_back_to_ascii(self):
        out = camera_view.draw_text_bar(np.full((480, 640, 3), 200, np.uint8), "Ready 3/6", None)
        self.assertGreater(out[:30].max(), 230)

    def test_decorated_frame_has_the_centre_cross(self):
        view = self.view()
        out = view._decorate(np.zeros((480, 640, 3), np.uint8))
        self.assertTrue(out[240, 320].any(), "a cross marks the centre of the table")

    def test_window_only_with_a_desktop_and_not_disabled(self):
        wanted = camera_view.preview_wanted
        self.assertTrue(wanted({"DISPLAY": ":0"}))
        self.assertTrue(wanted({"WAYLAND_DISPLAY": "wayland-0"}))
        self.assertFalse(wanted({}))
        for off in ("0", "false", "no", "off"):
            self.assertFalse(wanted({"DISPLAY": ":0", "SCAN_PREVIEW": off}))


if __name__ == "__main__":
    unittest.main()
