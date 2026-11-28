"""Live webcam view for the turntable scanner.

CameraView owns the webcam: the main loop calls ``update()`` all the time, which reads the newest
frame and, when a desktop is available, shows it in a window with the current status (ready,
photo 3/6, turning, processing...). Photos for a scan are taken from the same feed with
``photo(after=...)``, so the window keeps running while the scan works in another thread.
Keys pressed in the window (SPACE, R, Q) are returned by ``poll_key()``.

Qt (the OpenCV window toolkit here) must be driven from the main thread: only ``update()`` touches
the window. Without a desktop (SSH, tests) or with SCAN_PREVIEW=0 no window is opened.
"""
import os
import threading
import time

import cv2
import numpy as np

WINDOW = "ArchoAI camera"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_DIR = os.path.dirname(FONT)

# The Qt bundled with opencv-python ships no fonts, and importing cv2 points QT_QPA_FONTDIR at its
# empty font folder (a warning on every start). Use the system fonts instead; Qt reads the variable
# only when the first window opens, so setting it after the import is in time.
if os.path.isdir(FONT_DIR) and not os.path.isdir(os.environ.get("QT_QPA_FONTDIR", "")):
    os.environ["QT_QPA_FONTDIR"] = FONT_DIR
# The bundled Qt has only the X11 (xcb) plugin; on the Pi's Wayland desktop XWayland provides it.
# Choosing it directly avoids a "could not find the Qt platform plugin wayland" message.
if os.environ.get("DISPLAY") and not os.environ.get("QT_QPA_PLATFORM"):
    os.environ["QT_QPA_PLATFORM"] = "xcb"


def preview_wanted(env=None):
    """A window only when asked (default yes) and a desktop is reachable."""
    env = os.environ if env is None else env
    if (env.get("SCAN_PREVIEW") or "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    return bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))


class CameraView:
    def __init__(self, cap, show=True, title=WINDOW):
        self.cap = cap
        self.title = title
        self.show = show
        self._lock = threading.Lock()
        self._frame = None
        self._frame_time = 0.0
        self._status = ""
        self._keys = []
        self._window_open = False
        self._font = None
        if show:
            self._open_window()

    # --- called from any thread -------------------------------------------------------------------

    def set_status(self, text):
        with self._lock:
            self._status = text

    def photo(self, after, timeout=5.0):
        """JPEG bytes of the first frame read after ``after`` (time.monotonic()); waits for it."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._frame is not None and self._frame_time > after:
                    frame = self._frame.copy()
                    break
            time.sleep(0.01)
        else:
            raise RuntimeError("Камера не вернула кадр")
        ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok:
            raise RuntimeError("Не удалось сжать кадр в JPEG")
        return buffer.tobytes()

    # --- called from the main thread only ---------------------------------------------------------

    def update(self):
        """Reads one frame and refreshes the window. Paces the main loop (one camera frame)."""
        ok, frame = self.cap.read()
        if ok and frame is not None:
            with self._lock:
                self._frame, self._frame_time = frame, time.monotonic()
        elif not self._window_open:
            time.sleep(0.03)
        if not self._window_open:
            return
        try:
            if frame is not None:
                cv2.imshow(self.title, self._decorate(frame))
            key = cv2.waitKey(1) & 0xFF
            if key != 255:
                self._keys.append(chr(key).lower())
            if cv2.getWindowProperty(self.title, cv2.WND_PROP_VISIBLE) < 1:  # closed with the mouse
                self._window_open = False
        except cv2.error:
            self._window_open = False

    def poll_key(self):
        """A key pressed in the window since the last call ('r' for SPACE/R, 'q' for Q/Esc), or None."""
        while self._keys:
            key = self._keys.pop(0)
            if key in (" ", "r"):
                return "r"
            if key in ("q", "\x1b"):
                return "q"
        return None

    def drop_keys(self):
        self._keys.clear()

    def close(self):
        if self._window_open:
            try:
                cv2.destroyWindow(self.title)
                cv2.waitKey(1)
            except cv2.error:
                pass
            self._window_open = False

    @property
    def window_open(self):
        return self._window_open

    # --- drawing ----------------------------------------------------------------------------------

    def _open_window(self):
        try:
            cv2.namedWindow(self.title, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(self.title, 720, 540)  # fits a 1024 x 768 screen; drag to resize
            cv2.moveWindow(self.title, 290, 40)
            self._window_open = True
        except cv2.error:
            self._window_open = False

    def _decorate(self, frame):
        """The frame with a centre cross (to place the artifact) and the status line."""
        out = frame.copy()
        h, w = out.shape[:2]
        cx, cy, arm = w // 2, h // 2, max(12, min(w, h) // 20)
        cv2.line(out, (cx - arm, cy), (cx + arm, cy), (0, 245, 160), 1, cv2.LINE_AA)
        cv2.line(out, (cx, cy - arm), (cx, cy + arm), (0, 245, 160), 1, cv2.LINE_AA)
        with self._lock:
            status = self._status
        if status:
            out = draw_text_bar(out, status, self._get_font(h))
        return out

    def _get_font(self, height):
        size = max(14, height // 28)
        if self._font is None or self._font[0] != size:
            try:
                from PIL import ImageFont
                self._font = (size, ImageFont.truetype(FONT, size))
            except (ImportError, OSError):
                self._font = (size, None)
        return self._font[1]


def draw_text_bar(frame, text, font):
    """A dark bar with ``text`` at the top of the frame. Pillow draws Cyrillic; OpenCV only ASCII."""
    h, w = frame.shape[:2]
    bar = max(28, h // 16)
    frame[:bar] = (frame[:bar] * 0.35).astype(np.uint8)
    if font is not None:
        from PIL import Image, ImageDraw
        image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        ImageDraw.Draw(image).text((10, (bar - font.size) // 2), text, font=font, fill=(255, 255, 255))
        return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
    ascii_text = text.encode("ascii", "replace").decode()
    cv2.putText(frame, ascii_text, (10, bar - 9), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
    return frame
