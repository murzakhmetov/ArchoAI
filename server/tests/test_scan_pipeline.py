"""End-to-end test of the scanner scripts against the ArchoAI server on the Pi.

withbutton.py and withbutton_manual.py run one complete scan each (scan_harness.py fakes the
camera and the Tripo / Gemini APIs; DeepCrack, the database and the storage are real). Then the
test checks what a phone would see: the artifact row, the crack-marked photo and the 3D model,
all served by the Pi. Everything the scan created is removed afterwards.

    scripts/scripts/venv/bin/python -m unittest discover -s server/tests -v
"""
import os
import pathlib
import subprocess
import sys
import unittest
import uuid

import requests

from test_server_api import BASE, PUBLISHABLE, ROOT, TIMEOUT, ServerTestCase, admin_headers

SCRIPTS = ROOT / "scripts" / "scripts"
HARNESS = pathlib.Path(__file__).resolve().parent / "scan_harness.py"
VENV_PYTHON = SCRIPTS / "venv" / "bin" / "python"
PUBLIC = f"{BASE}/storage/v1/object/public/"


class ScanPipelineTests(ServerTestCase):

    def run_scan(self, script_name, keys=None, **extra_env):
        if not (SCRIPTS / "pretrained_net_G.pth").exists():
            self.skipTest("DeepCrack weights pretrained_net_G.pth are missing")
        name = f"Selftest scan {uuid.uuid4().hex[:8]}"
        env = dict(os.environ, SUPABASE_URL=BASE, SUPABASE_KEY=PUBLISHABLE, HARNESS_ARTIFACT_NAME=name,
                   PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8",
                   # Only the Pi: a second server from archoai_app/.env (the cloud) must not get test data.
                   SUPABASE_FALLBACK_URL="", SUPABASE_FALLBACK_KEY="", SUPABASE_SERVER="primary",
                   SCAN_PREVIEW="0", **extra_env)  # no camera window on the desktop during tests
        env.pop("SUPABASE_PROJECT_REF", None)
        python = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
        done = subprocess.run([python, str(HARNESS), str(SCRIPTS / script_name)], env=env, input=keys,
                              capture_output=True, text=True, encoding="utf-8", timeout=900)
        output = done.stdout + done.stderr
        rows = self.admin_select(f"artifacts?name=eq.{requests.utils.quote(name)}&select=*")
        if not rows and "без анализа" in done.stdout:  # saved without Gemini: found by its scan folder
            rows = self.admin_select("artifacts?name=like.*" + requests.utils.quote("(без анализа)") + "&select=*")
        for row in rows:  # clean-ups run in reverse order: artifact, model_files, then the files
            for url in [row.get("image_path"), row.get("model_url"), *(row.get("photo_urls") or [])]:
                if url and url.startswith(PUBLIC):
                    bucket, path = url[len(PUBLIC):].split("/", 1)
                    self.delete_object(bucket, path)
        self.addCleanup(lambda: self.rest("DELETE", f"model_files?tripo_task_id=eq.task-e2e-{name.split()[-1]}",
                                          admin_headers()))
        for row in rows:
            self.delete_rows("artifacts", "id", row["id"])
        self.delete_rows("artifacts", "name", requests.utils.quote(name))
        self.assertEqual(done.returncode, 0, output[-3000:])
        self.assertIn("HARNESS_RESULT", output, output[-3000:])
        self.assertIn("УСПЕХ", output, "the script did not report a successful save:\n" + output[-3000:])
        return name, rows, output

    def check_scan(self, name, rows, output):
        self.assertEqual(len(rows), 1, f"expected one artifact named {name!r}:\n{output[-2000:]}")
        row = rows[0]
        self.assertEqual(row["status"], "complete")
        self.assertEqual((row["type"], row["era"], row["crack_percentage"]), ("Domestic", "Iron Age", 12.5))

        # withbutton*.py: artifact_analyzed_<time>.jpg; turntable_scan.py: scans/<scan id>/analyzed.jpg
        self.assertTrue(row["image_path"].startswith(PUBLIC + "artifact-images/artifact_analyzed_")
                        or (row["image_path"].startswith(PUBLIC + "artifact-images/scans/")
                            and row["image_path"].endswith("/analyzed.jpg")), row["image_path"])
        photo = requests.get(row["image_path"], timeout=TIMEOUT)
        self.assertEqual(photo.status_code, 200)
        self.assertTrue(photo.content.startswith(b"\xff\xd8\xff"), "the stored photo is not a JPEG")

        self.assertTrue(row["model_url"].startswith(PUBLIC + "models-0"), row["model_url"])
        model = requests.get(row["model_url"], timeout=TIMEOUT)
        self.assertEqual(model.status_code, 200)
        self.assertTrue(model.content.startswith(b"glTF"), "the stored model is not a GLB")

        files = self.admin_select(f"model_files?tripo_task_id=eq.task-e2e-{name.split()[-1]}&select=source,size_bytes")
        self.assertEqual(files, [{"source": "tripo", "size_bytes": len(model.content)}])

        result = output.split("HARNESS_RESULT ", 1)[1].splitlines()[0]
        for call in ('"POST", "/v3/files"', '"/v3/generation/multiview-to-model"', '"GET", "/model.glb"',
                     ':generateContent"'):
            self.assertIn(call, result, f"the scan did not call {call}")
        self.assertEqual(result.count('"POST", "/v3/files"'), 4, "four photos go to Tripo")
        self.assertIn('"camera_indexes": [0]', result, "CAMERA_INDEX from the environment was not used")

    def test_withbutton_scan_is_stored_on_the_pi(self):
        self.check_scan(*self.run_scan("withbutton.py"))

    def test_withbutton_manual_scan_is_stored_on_the_pi(self):
        self.check_scan(*self.run_scan("withbutton_manual.py"))

    def test_turntable_scan_is_stored_on_the_pi(self):
        # One line on stdin starts one scan (as SPACE does in a terminal), the end of input quits.
        name, rows, output = self.run_scan("turntable_scan.py", keys="scan\n")
        self.check_scan(name, rows, output)
        result = output.split("HARNESS_RESULT ", 1)[1].splitlines()[0]
        self.assertIn('"rotations": [60.0, 60.0, 60.0, 60.0, 60.0, 60.0]', result,
                      "the table must turn 6 x 60 degrees, one full turn back to the start")
        photos = rows[0]["photo_urls"]
        self.assertEqual(len(photos), 6)
        self.assertTrue(rows[0]["image_path"].endswith("/analyzed.jpg"))
        for degrees, url in zip(range(0, 360, 60), photos):
            self.assertTrue(url.startswith(PUBLIC + "artifact-images/scans/") and url.endswith(f"angle_{degrees:03d}.jpg"), url)
            photo = requests.get(url, timeout=TIMEOUT)
            self.assertEqual(photo.status_code, 200, url)
            self.assertTrue(photo.content.startswith(b"\xff\xd8\xff"), f"{url} is not a JPEG")


    def test_turntable_scan_survives_a_busy_gemini(self):
        # Two 503 answers ("high demand"), then Gemini answers: retries keep the analysis.
        name, rows, output = self.run_scan("turntable_scan.py", keys="scan\n", HARNESS_GEMINI_BUSY="2")
        self.check_scan(name, rows, output)
        self.assertIn("занят", output)

    def test_turntable_scan_is_kept_when_gemini_stays_down(self):
        # Every Gemini call answers 503: the photos and the model are still saved, as "pending".
        name, rows, output = self.run_scan("turntable_scan.py", keys="scan\n", HARNESS_GEMINI_BUSY="99",
                                           GEMINI_FALLBACK_MODELS="gemini-2.5-flash-lite")
        self.assertIn("без анализа", output, output[-2000:])
        self.assertEqual(len(rows), 1, output[-2000:])
        row = rows[0]
        self.assertEqual(row["status"], "complete" if row.get("model_url") else "pending")
        self.assertEqual(len(row["photo_urls"]), 6)
        self.assertTrue(row["model_url"], "the 3D model must still be saved")
        self.assertEqual(requests.get(row["photo_urls"][0], timeout=TIMEOUT).status_code, 200)


if __name__ == "__main__":
    unittest.main()
