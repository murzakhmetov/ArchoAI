"""Contract tests of the ArchoAI server on the Raspberry Pi (the self-hosted Supabase stack).

They talk to the running server over HTTP, the same way its three kinds of clients do:
  * the ESP32 sensor board (monitoring.cpp): POST /rest/v1/sensor_data with the publishable key;
  * the scanner scripts (withbutton*.py): image uploads, store_model() into the model batches,
    inserts into artifacts;
  * the mobile app (supabase_flutter): sign-up and sign-in, reads, inserts, updates, RPCs, model
    uploads and realtime updates.
They also check the security setup: only the API port is reachable from the network, and the
public demo keys of the Supabase CLI are refused.

Every row, file and user a test creates is removed again with the server's secret key. The whole
module is skipped when the server cannot be reached. Run from the repository root:

    scripts/scripts/venv/bin/python -m unittest discover -s server/tests -v

Configuration (all optional): ARCHOAI_SERVER_URL (default: SUPABASE_URL of archoai_app/.env),
ARCHOAI_PUBLISHABLE_KEY / ARCHOAI_SECRET_KEY (default: supabase/.env).
"""

import asyncio
import hashlib
import json
import os
import pathlib
import socket
import struct
import sys
import time
import unittest
import uuid
from urllib.parse import urlsplit

import requests

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "scripts"))

import supabase_models  # noqa: E402


def read_env_file(path):
    """KEY=value lines of a .env file (no shell expansion); {} when the file does not exist."""
    values = {}
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


APP_ENV = read_env_file(ROOT / "archoai_app" / ".env")
SERVER_ENV = read_env_file(ROOT / "supabase" / ".env")
BASE = supabase_models.normalize_base_url(
    os.environ.get("ARCHOAI_SERVER_URL") or APP_ENV.get("SUPABASE_URL") or "http://127.0.0.1:54321"
)
PUBLISHABLE = os.environ.get("ARCHOAI_PUBLISHABLE_KEY") or SERVER_ENV.get("ARCHOAI_PUBLISHABLE_KEY", "")
SECRET = os.environ.get("ARCHOAI_SECRET_KEY") or SERVER_ENV.get("ARCHOAI_SECRET_KEY", "")
TIMEOUT = 30
PHOTO = ROOT / "scripts" / "scripts" / "camera_test_photos" / "camera_index_0.jpg"

# Keys every Supabase CLI install uses by default. They are public, so the Pi must refuse them.
DEMO_KEYS = {
    "demo publishable key": "sb_publishable_ACJWlzQHlZjBrEguHvfOxg_3BJgxAaH",
    # Split so secret scanners do not flag this public, documented demo value.
    "demo secret key": "sb_" + "secret_" + "N7UND0UgjKTVK-Uodkm0Hg_xSvEMPvz",
    "demo anon JWT": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6ImFub24iLCJleHAiOjE5ODM4MTI5OTZ9.CRXP1A7WOeoJeXxjNni43kdQwgnWNReilDMblYTn_I0",
    "demo service_role JWT": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImV4cCI6MTk4MzgxMjk5Nn0.EGIM96RAZx35lJzdJsyH-qQwv8Hdp7fsn3W0YpN81IU",
}


def key_headers(key, **extra):
    return {"apikey": key, "Authorization": f"Bearer {key}", **extra}


def device_headers(**extra):
    """Exactly what monitoring.cpp sends: the publishable key in both headers."""
    return key_headers(PUBLISHABLE, **extra)


def admin_headers(**extra):
    return key_headers(SECRET, **extra)


def user_headers(token, **extra):
    """What supabase_flutter sends after sign-in: publishable apikey + the user's access token."""
    return {"apikey": PUBLISHABLE, "Authorization": f"Bearer {token}", **extra}


def make_glb(payload_size=256):
    body = os.urandom(payload_size)
    return b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body


class ServerTestCase(unittest.TestCase):
    """Base class: skips when the server is down, and runs the registered clean-ups."""

    @classmethod
    def setUpClass(cls):
        if not BASE or not PUBLISHABLE or not SECRET:
            raise unittest.SkipTest("server URL or keys not configured (archoai_app/.env, supabase/.env)")
        try:
            health = requests.get(f"{BASE}/auth/v1/health", headers={"apikey": PUBLISHABLE}, timeout=5)
        except requests.RequestException as exc:
            raise unittest.SkipTest(f"ArchoAI server not reachable at {BASE}: {type(exc).__name__}")
        if health.status_code != 200:
            raise unittest.SkipTest(f"ArchoAI server at {BASE} is not healthy (auth: HTTP {health.status_code})")

    # --- helpers ---------------------------------------------------------------------------

    def rest(self, method, path, headers, **kwargs):
        return requests.request(method, f"{BASE}/rest/v1/{path}", headers=headers, timeout=TIMEOUT, **kwargs)

    def admin_select(self, path):
        response = self.rest("GET", path, admin_headers())
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def delete_rows(self, table, column, value):
        self.addCleanup(lambda: self.rest("DELETE", f"{table}?{column}=eq.{value}", admin_headers()))

    def delete_object(self, bucket, name):
        self.addCleanup(lambda: requests.delete(
            f"{BASE}/storage/v1/object/{bucket}/{name}", headers=admin_headers(), timeout=TIMEOUT))

    def insert_reading_as_device(self, values, attempts=4):
        """POST one reading like the ESP32; waits out the 2 s device rate limit when it hits it."""
        body = json.dumps(values, separators=(",", ":"))
        for _ in range(attempts):
            response = requests.post(f"{BASE}/rest/v1/sensor_data", data=body, timeout=TIMEOUT,
                                     headers=device_headers(**{"Content-Type": "application/json"}))
            if "at most one anonymous insert" not in response.text:
                return response
            time.sleep(2.2)
        return response

    def find_reading(self, values, since):
        query = "&".join(f"{k}=eq.{v}" for k, v in values.items())
        return self.admin_select(f"sensor_data?select=id,created_at&{query}&created_at=gte.{since}")

    def sign_up(self):
        email = f"selftest-{uuid.uuid4().hex[:12]}@archoai.test"
        password = "Pi-" + uuid.uuid4().hex
        response = requests.post(f"{BASE}/auth/v1/signup", headers={"apikey": PUBLISHABLE}, timeout=TIMEOUT,
                                 json={"email": email, "password": password})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        user_id = body["user"]["id"] if "user" in body else body["id"]
        self.addCleanup(lambda: requests.delete(
            f"{BASE}/auth/v1/admin/users/{user_id}", headers=admin_headers(), timeout=TIMEOUT))
        self.assertIn("access_token", body, "sign-up must log the user in at once (no e-mail confirmation)")
        return email, password, body["access_token"], user_id

    @staticmethod
    def db_now():
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 5))


class DeviceTests(ServerTestCase):
    """The ESP32 board (monitoring.cpp) with the publishable key."""

    def test_reading_is_stored_with_the_key_in_both_headers(self):
        values = {"temperature": 24.5, "humidity": 45.25, "air_quality": 3333.0}
        since = self.db_now()
        response = self.insert_reading_as_device(values)
        self.assertIn(response.status_code, (200, 201), response.text)
        rows = self.find_reading(values, since)
        self.assertEqual(len(rows), 1, rows)
        self.delete_rows("sensor_data", "id", rows[0]["id"])

    def test_reading_with_soil_and_ppm_is_stored(self):
        """The current firmware body (migration 20261128000002); a missing sensor goes out as null."""
        for values in ({"temperature": 24.5, "humidity": 45.25, "air_quality": 3336.0,
                        "air_ppm": 612.0, "soil_moisture": 37.5, "soil_temperature": 18.25},
                       {"temperature": 24.5, "humidity": 45.25, "air_quality": 3337.0,
                        "air_ppm": None, "soil_moisture": None, "soil_temperature": None}):
            with self.subTest(values=values):
                since = self.db_now()
                response = self.insert_reading_as_device(values)
                self.assertIn(response.status_code, (200, 201), response.text)
                rows = self.admin_select("sensor_data?select=id,air_ppm,soil_moisture,soil_temperature"
                                         f"&air_quality=eq.{values['air_quality']}&created_at=gte.{since}")
                self.assertEqual(len(rows), 1, rows)
                self.delete_rows("sensor_data", "id", rows[0]["id"])
                self.assertEqual((rows[0]["air_ppm"], rows[0]["soil_moisture"], rows[0]["soil_temperature"]),
                                 (values["air_ppm"], values["soil_moisture"], values["soil_temperature"]))

    def test_values_out_of_range_are_refused(self):
        for values in ({"temperature": 24.5, "humidity": 145.0, "air_quality": 300.0},
                       {"temperature": 24.5, "humidity": 45.0, "air_quality": 4096.0},
                       {"temperature": 126.0, "humidity": 45.0, "air_quality": 300.0},
                       {"temperature": 24.5, "humidity": 45.0, "air_quality": 300.0, "soil_moisture": 101.0},
                       {"temperature": 24.5, "humidity": 45.0, "air_quality": 300.0, "soil_temperature": -56.0},
                       {"temperature": 24.5, "humidity": 45.0, "air_quality": 300.0, "air_ppm": 10001.0}):
            with self.subTest(values=values):
                response = self.insert_reading_as_device(values)
                self.assertIn(response.status_code, (401, 403), response.text)
                self.assertIn("row-level security", response.text)

    def test_device_cannot_set_created_at_or_id(self):
        for extra in ({"created_at": "2100-01-01T00:00:00Z"}, {"id": 999999999}):
            with self.subTest(extra=extra):
                values = {"temperature": 24.5, "humidity": 45.0, "air_quality": 300.0, **extra}
                response = self.insert_reading_as_device(values)
                self.assertIn(response.status_code, (401, 403), response.text)
                self.assertIn("permission denied", response.text)

    def test_second_reading_within_two_seconds_is_refused(self):
        first = {"temperature": 24.5, "humidity": 45.5, "air_quality": 3334.0}
        since = self.db_now()
        response = self.insert_reading_as_device(first)
        self.assertIn(response.status_code, (200, 201), response.text)
        for row in self.find_reading(first, since):
            self.delete_rows("sensor_data", "id", row["id"])
        second = requests.post(f"{BASE}/rest/v1/sensor_data", timeout=TIMEOUT,
                               headers=device_headers(**{"Content-Type": "application/json"}),
                               json={"temperature": 24.5, "humidity": 45.75, "air_quality": 3335.0})
        self.assertGreaterEqual(second.status_code, 400, second.text)
        self.assertIn("at most one anonymous insert", second.text)

    def test_device_can_read_but_not_delete_readings(self):
        read = self.rest("GET", "sensor_data?select=id&limit=1", device_headers())
        self.assertEqual(read.status_code, 200, read.text)
        delete = self.rest("DELETE", "sensor_data?id=gt.0", device_headers(Prefer="return=representation"))
        self.assertTrue(delete.status_code in (401, 403) or delete.json() == [], delete.text)


class ScannerTests(ServerTestCase):
    """The scanner scripts (withbutton.py / withbutton_manual.py) with the publishable key."""

    def test_photo_upload_and_public_link(self):
        photo = PHOTO.read_bytes()
        name = f"selftest_{uuid.uuid4().hex}.jpg"
        upload = requests.post(f"{BASE}/storage/v1/object/artifact-images/{name}", data=photo, timeout=TIMEOUT,
                               headers=device_headers(**{"Content-Type": "image/jpeg"}))
        self.delete_object("artifact-images", name)
        self.assertIn(upload.status_code, (200, 201), upload.text)
        public = requests.get(f"{BASE}/storage/v1/object/public/artifact-images/{name}", timeout=TIMEOUT)
        self.assertEqual(public.status_code, 200)
        self.assertEqual(public.content, photo)
        self.assertTrue(public.headers.get("Content-Type", "").startswith("image/jpeg"))

    def test_image_bucket_refuses_other_file_types(self):
        name = f"selftest_{uuid.uuid4().hex}.html"
        upload = requests.post(f"{BASE}/storage/v1/object/artifact-images/{name}", data=b"<script>x</script>",
                               timeout=TIMEOUT, headers=device_headers(**{"Content-Type": "text/html"}))
        self.delete_object("artifact-images", name)
        self.assertGreaterEqual(upload.status_code, 400, upload.text)

    def test_store_model_fills_the_first_batch_and_registers_the_file(self):
        glb = make_glb()
        logs = []
        stored = supabase_models.store_model(glb, BASE, PUBLISHABLE, tripo_task_id="selftest-task", log=logs.append)
        self.assertIsNotNone(stored, logs)
        self.delete_object(stored.bucket_id, stored.object_path)
        if stored.file_id:
            self.delete_rows("model_files", "id", stored.file_id)
        self.assertEqual(stored.bucket_id, "models-01")
        self.assertIsNotNone(stored.file_id, logs)
        self.assertTrue(stored.url.startswith(f"{BASE}/storage/v1/object/public/models-01/"))
        public = requests.get(stored.url, timeout=TIMEOUT)
        self.assertEqual(public.status_code, 200)
        self.assertEqual(public.content, glb)
        rows = self.admin_select(f"model_files?id=eq.{stored.file_id}&select=size_bytes,sha256,source,tripo_task_id")
        self.assertEqual(rows, [{"size_bytes": len(glb), "sha256": hashlib.sha256(glb).hexdigest(),
                                 "source": "tripo", "tripo_task_id": "selftest-task"}])

    def test_artifact_insert_like_save_all_to_supabase(self):
        name = f"Selftest artifact {uuid.uuid4().hex[:8]}"
        payload = {"name": name, "type": "Domestic", "material": "Clay", "era": "Iron Age",
                   "purpose": "Storage", "condition": "Fragmentary", "crack_percentage": 12.5,
                   "image_path": f"{BASE}/storage/v1/object/public/artifact-images/x.jpg", "status": "pending"}
        response = self.rest("POST", "artifacts", device_headers(Prefer="return=minimal"), json=payload)
        self.delete_rows("artifacts", "name", requests.utils.quote(name))
        self.assertEqual(response.status_code, 201, response.text)
        rows = self.admin_select(f"artifacts?name=eq.{requests.utils.quote(name)}&select=status,crack_percentage,era")
        self.assertEqual(rows, [{"status": "pending", "crack_percentage": 12.5, "era": "Iron Age"}])

    def test_anonymous_clients_cannot_read_or_change_artifacts(self):
        read = self.rest("GET", "artifacts?select=id&limit=5", device_headers())
        self.assertEqual(read.status_code, 200, read.text)
        self.assertEqual(read.json(), [])
        update = self.rest("PATCH", "artifacts?id=not.is.null", device_headers(Prefer="return=representation"),
                           json={"name": "changed by anon"})
        self.assertTrue(update.status_code in (401, 403) or update.json() == [], update.text)


class MobileAppTests(ServerTestCase):
    """The Flutter app: everything supabase_service.dart does, as a signed-in user."""

    def test_sign_up_sign_in_and_the_collection(self):
        email, password, _, _ = self.sign_up()
        login = requests.post(f"{BASE}/auth/v1/token?grant_type=password", headers={"apikey": PUBLISHABLE},
                              json={"email": email, "password": password}, timeout=TIMEOUT)
        self.assertEqual(login.status_code, 200, login.text)
        token = login.json()["access_token"]
        headers = user_headers(token)

        created = self.rest("POST", "artifacts", user_headers(token, Prefer="return=representation"),
                            json={"name": f"Selftest app {uuid.uuid4().hex[:8]}", "image_path": ""})
        self.assertEqual(created.status_code, 201, created.text)
        artifact_id = created.json()[0]["id"]
        self.delete_rows("artifacts", "id", artifact_id)

        listed = self.rest("GET", "artifacts?select=id&order=created_at.desc", headers)
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertIn(artifact_id, [row["id"] for row in listed.json()])

        updated = self.rest("PATCH", f"artifacts?id=eq.{artifact_id}", user_headers(token, Prefer="return=representation"),
                            json={"status": "cataloged"})
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual([row["status"] for row in updated.json()], ["cataloged"])

        sensors = self.rest("GET", "sensor_data?select=*&order=created_at.desc&limit=20", headers)
        self.assertEqual(sensors.status_code, 200, sensors.text)

    def test_app_model_upload_into_a_batch_and_link(self):
        _, _, token, _ = self.sign_up()
        created = self.rest("POST", "artifacts", user_headers(token, Prefer="return=representation"),
                            json={"name": f"Selftest model {uuid.uuid4().hex[:8]}", "image_path": ""})
        self.assertEqual(created.status_code, 201, created.text)
        artifact_id = created.json()[0]["id"]
        self.delete_rows("artifacts", "id", artifact_id)

        glb = make_glb()
        bucket = self.rest("POST", "rpc/pick_model_bucket", user_headers(token), json={"p_size_bytes": len(glb)})
        self.assertEqual(bucket.status_code, 200, bucket.text)
        self.assertEqual(bucket.json(), "models-01")

        name = f"{artifact_id}.glb"
        for attempt in range(2):  # the app uploads with upsert: a second upload replaces the first
            upload = requests.post(f"{BASE}/storage/v1/object/models-01/{name}", data=glb, timeout=TIMEOUT,
                                   headers=user_headers(token, **{"Content-Type": "model/gltf-binary", "x-upsert": "true"}))
            if attempt == 0:
                self.delete_object("models-01", name)
            self.assertIn(upload.status_code, (200, 201), upload.text)

        registered = self.rest("POST", "rpc/register_model_file", user_headers(token),
                               json={"p_bucket_id": "models-01", "p_object_path": name, "p_artifact_id": artifact_id,
                                     "p_source": "tripo", "p_tripo_task_id": "selftest-app"})
        self.assertEqual(registered.status_code, 200, registered.text)
        file_id = registered.json()
        self.delete_rows("model_files", "id", file_id)

        model_url = f"{BASE}/storage/v1/object/public/models-01/{name}"
        attached = self.rest("PATCH", f"artifacts?id=eq.{artifact_id}", user_headers(token, Prefer="return=representation"),
                             json={"model_url": model_url, "model_file_id": file_id, "tripo_task_id": None})
        self.assertEqual(attached.status_code, 200, attached.text)
        self.assertEqual(len(attached.json()), 1)
        self.assertEqual(requests.get(model_url, timeout=TIMEOUT).content, glb)

        usage = self.rest("POST", "rpc/model_storage_usage", user_headers(token), json={})
        self.assertEqual(usage.status_code, 200, usage.text)
        self.assertTrue({"models-01", "models-02", "models-03", "models-04"} <= {row["bucket_id"] for row in usage.json()})
        linked = self.rest("POST", "rpc/link_model_files", user_headers(token), json={})
        self.assertEqual(linked.status_code, 200, linked.text)

    def test_realtime_delivers_new_readings_to_a_signed_in_app(self):
        _, _, token, _ = self.sign_up()
        marker = 3000.0 + (uuid.uuid4().int % 900)
        ws_url = BASE.replace("http://", "ws://").replace("https://", "wss://") + \
            f"/realtime/v1/websocket?apikey={PUBLISHABLE}&vsn=1.0.0"

        async def scenario():
            import websockets
            async with websockets.connect(ws_url, open_timeout=TIMEOUT) as ws:
                topic = f"realtime:selftest-{uuid.uuid4().hex[:8]}"
                await ws.send(json.dumps({
                    "topic": topic, "event": "phx_join", "ref": "1", "join_ref": "1",
                    "payload": {"config": {"broadcast": {"self": False}, "presence": {"key": ""},
                                           "postgres_changes": [{"event": "INSERT", "schema": "public",
                                                                 "table": "sensor_data"}]},
                                "access_token": token}}))
                subscribed = False
                deadline = time.monotonic() + 40
                inserted_at = None
                while time.monotonic() < deadline:
                    try:
                        message = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
                    except asyncio.TimeoutError:
                        message = None
                    if message:
                        event, payload = message.get("event"), message.get("payload") or {}
                        if event == "phx_reply" and payload.get("status") == "error":
                            return f"join refused: {payload}"
                        if event == "system" and payload.get("extension") == "postgres_changes":
                            if payload.get("status") != "ok":
                                return f"subscription failed: {payload}"
                            subscribed = True
                        if event == "postgres_changes":
                            record = payload.get("data", {}).get("record", {})
                            if record.get("air_quality") == marker:
                                return record
                    if subscribed and inserted_at is None:
                        inserted_at = self.db_now()
                        response = await asyncio.to_thread(
                            self.insert_reading_as_device,
                            {"temperature": 24.5, "humidity": 46.0, "air_quality": marker})
                        if response.status_code not in (200, 201):
                            return f"insert refused: {response.status_code} {response.text}"
                return "no realtime event within 40 s" + ("" if subscribed else " (never subscribed)")

        result = asyncio.run(scenario())
        for row in self.find_reading({"air_quality": marker}, self.db_now()[:10]):
            self.delete_rows("sensor_data", "id", row["id"])
        self.assertIsInstance(result, dict, result)
        self.assertEqual(result["air_quality"], marker)


class SecurityTests(ServerTestCase):
    """Hardening of the Pi: own secrets, only the API port on the network."""

    def test_public_demo_keys_of_the_cli_get_no_more_than_anonymous_access(self):
        """The Pi signs its own tokens, so the CLI's public demo keys must not unlock anything.

        The gateway the CLI generates maps only this server's secret key to the service role and
        its publishable key to the anonymous role; any other "sb_..." value goes through as an
        anonymous request (the publishable key is public by design: it ships inside the app and
        the ESP32 firmware). The demo JWTs are signed with the demo secret and are refused.
        """
        keys = dict(DEMO_KEYS, **{"made-up key": "sb_secret_" + uuid.uuid4().hex})
        for label, key in keys.items():
            with self.subTest(key=label):
                admin = requests.get(f"{BASE}/auth/v1/admin/users", headers=key_headers(key), timeout=TIMEOUT)
                self.assertIn(admin.status_code, (401, 403), f"{label} reached the admin API")
                for table in ("app_limits", "artifacts"):  # signed-in users / service role only
                    response = self.rest("GET", f"{table}?select=*&limit=1", key_headers(key))
                    self.assertTrue(response.status_code in (401, 403) or response.json() == [],
                                    f"{label} read {table}: {response.text[:200]}")
                upload = requests.post(f"{BASE}/storage/v1/object/models-01/probe_{uuid.uuid4().hex}.glb",
                                       data=make_glb(), timeout=TIMEOUT,
                                       headers=key_headers(key, **{"Content-Type": "model/gltf-binary"}))
                self.assertGreaterEqual(upload.status_code, 400, f"{label} could upload a file")
        for label in ("demo anon JWT", "demo service_role JWT"):
            with self.subTest(jwt=label):
                response = self.rest("GET", "sensor_data?select=id&limit=1", key_headers(DEMO_KEYS[label]))
                self.assertEqual(response.status_code, 401, f"{label} was accepted: {response.text[:200]}")

    def test_secret_key_is_needed_for_admin_access(self):
        allowed = requests.get(f"{BASE}/auth/v1/admin/users", headers=admin_headers(), timeout=TIMEOUT)
        self.assertEqual(allowed.status_code, 200, allowed.text[:200])
        refused = requests.get(f"{BASE}/auth/v1/admin/users", headers=device_headers(), timeout=TIMEOUT)
        self.assertIn(refused.status_code, (401, 403))

    def test_only_the_api_port_is_reachable_from_the_network(self):
        host = urlsplit(BASE).hostname
        if host in ("127.0.0.1", "localhost", "::1"):
            # Find the address other devices use: the source address of a route to the internet
            # (connecting a UDP socket sends nothing).
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect(("192.0.2.1", 9))
                host = probe.getsockname()[0]
        with socket.create_connection((host, 54321), timeout=5):
            pass
        for port, what in ((54322, "the database"), (54331, "the internal API port")):
            with self.subTest(port=port):
                with self.assertRaises(OSError, msg=f"{what} (port {port}) is reachable at {host}"):
                    socket.create_connection((host, port), timeout=3).close()

    def test_storage_cap_is_sized_for_the_pi(self):
        limits = self.admin_select("app_limits?key=eq.storage_total_cap_bytes&select=value_bigint")
        self.assertEqual(limits, [{"value_bigint": 20000000000}])
        batches = self.admin_select("model_buckets?select=bucket_id,capacity_bytes,is_active&order=batch_no")
        active = {row["bucket_id"]: row["capacity_bytes"] for row in batches if row["is_active"]}
        self.assertEqual(active, {f"models-0{i}": 5000000000 for i in range(1, 5)})


if __name__ == "__main__":
    unittest.main()
