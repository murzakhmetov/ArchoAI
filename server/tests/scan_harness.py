"""Runs a scanner script (withbutton.py or withbutton_manual.py) for one scan, without hardware or
paid APIs, against the real ArchoAI server. Used by test_scan_pipeline.py.

Real: the script itself, the DeepCrack model and OpenCV, the Tripo client, the google-genai SDK,
supabase_models and the server on the Pi (database, storage, policies).
Fake: the camera (replays the photos in scripts/scripts/camera_test_photos), the ENTER presses,
and the Tripo and Gemini APIs (a local HTTP server). Requests to any other host are refused, so
nothing leaves the Pi.

    python scan_harness.py <path of the script>

Settings come from the environment (the test sets them): SUPABASE_URL, SUPABASE_KEY and
HARNESS_ARTIFACT_NAME (the name the fake Gemini gives the artifact). Prints one line
"HARNESS_RESULT {json}" at the end.
"""
import builtins
import json
import os
import pathlib
import runpy
import struct
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

script = pathlib.Path(sys.argv[1]).resolve()
scripts_dir = script.parent
GLB = b"glTF" + struct.pack("<II", 2, 12 + 64) + b"\x00" * 64
TASK_ID = "task-e2e-" + os.environ["HARNESS_ARTIFACT_NAME"].split()[-1]
ANALYSIS = {"name": os.environ["HARNESS_ARTIFACT_NAME"], "type": "Domestic", "material": "Terracotta",
            "era": "Iron Age", "purpose": "Storage", "condition": "Fragmentary", "crack_percentage": 12.5}
calls = []
polls = {"n": 0}


class FakeApis(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status, body, content_type="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        calls.append(["GET", self.path.split("?")[0]])
        if self.path.startswith("/v3/tasks/"):
            polls["n"] += 1
            if polls["n"] == 1:
                return self._send(200, {"code": 0, "data": {"task_id": TASK_ID, "status": "running", "progress": 40}})
            return self._send(200, {"code": 0, "data": {
                "task_id": TASK_ID, "status": "success", "progress": 100,
                "output": {"model_url": f"http://127.0.0.1:{PORT}/model.glb?signature=x"}}})
        if self.path.startswith("/model.glb"):
            return self._send(200, GLB, "model/gltf-binary")
        self._send(404, {})

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        calls.append(["POST", self.path.split("?")[0]])
        if self.path == "/v3/files":
            if b"\xff\xd8\xff" not in body:
                return self._send(400, {"code": 2004, "message": "upload is not a JPEG"})
            return self._send(200, {"code": 0, "data": {"file_token": f"token-{len(calls)}"}})
        if self.path.startswith("/v3/generation/"):
            return self._send(200, {"code": 0, "data": {"task_id": TASK_ID}})
        if ":generateContent" in self.path:
            text = "```json\n" + json.dumps(ANALYSIS) + "\n```"
            return self._send(200, {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}}]})
        self._send(404, {})


server = ThreadingHTTPServer(("127.0.0.1", 0), FakeApis)
PORT = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()

os.environ.update({
    "GEMINI_API_KEY": "fake-gemini-key",
    "GOOGLE_GEMINI_BASE_URL": f"http://127.0.0.1:{PORT}",
    "TRIPO_API_KEY": "tsk_fake_key",
    "TRIPO_BASE_URL": f"http://127.0.0.1:{PORT}/v3",
    "CAMERA_INDEX": "0",
    "UPLOAD_GLB_TO_SUPABASE": "true",
})

import requests  # noqa: E402  (after the environment is set)

allowed_hosts = {"127.0.0.1", "localhost", urlsplit(os.environ["SUPABASE_URL"]).hostname}
original_request = requests.sessions.Session.request


def local_only(self, method, url, *args, **kwargs):
    if urlsplit(url).hostname not in allowed_hosts:
        raise RuntimeError(f"scan harness: blocked a request to {urlsplit(url).hostname}")
    return original_request(self, method, url, *args, **kwargs)


requests.sessions.Session.request = local_only

import cv2  # noqa: E402

PHOTOS = sorted((scripts_dir / "camera_test_photos").glob("*.jpg"))
opened = []


class ReplayCamera:
    """Every 16 reads (15 warm-up frames + the photo) moves on to the next sample photo."""

    def __init__(self, index, *args):
        opened.append(index)
        self.reads = 0

    def isOpened(self):
        return True

    def read(self):
        self.reads += 1
        return True, cv2.imread(str(PHOTOS[((self.reads - 1) // 16) % len(PHOTOS)]))

    def release(self):
        pass


cv2.VideoCapture = ReplayCamera

# withbutton.py: one ENTER starts a scan. withbutton_manual.py: one ENTER plus one per photo.
presses = {"left": 5 if "manual" in script.name else 1}


def press_enter(prompt=""):
    print(prompt)
    if presses["left"] == 0:
        raise KeyboardInterrupt  # the scripts end their loop on Ctrl+C
    presses["left"] -= 1
    return ""


builtins.input = press_enter
sys.path.insert(0, str(scripts_dir))
os.chdir(scripts_dir)
sys.argv = [str(script)]
runpy.run_path(str(script), run_name="__main__")
server.shutdown()
print("HARNESS_RESULT " + json.dumps({"camera_indexes": opened, "fake_api_calls": calls, "task_id": TASK_ID}))
