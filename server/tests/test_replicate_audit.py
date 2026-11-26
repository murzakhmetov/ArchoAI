"""Regression tests for the defects an independent audit found in the replicator (see replicate_fakes.py)."""
import datetime as dt
import http.server
import json
import os
import pathlib
import signal
import threading
import unittest
from unittest import mock

from replicate_fakes import BASE, LOCAL_PUBLIC, NOW, REMOTE, ReplicateCase, artifact, reading, reply
from replica import client
from replica.client import ServerError
from replica.util import parse_ts

OLD = "2026-11-20T10:00:00+00:00"
SYSTEMD = pathlib.Path(__file__).resolve().parents[1] / "systemd"


def serve(handler_class):
    server = http.server.HTTPServer(("127.0.0.1", 0), handler_class)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


class TransportTests(unittest.TestCase):
    def test_upload_body_is_sent_as_a_file_with_content_length(self):
        seen = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                seen["length"] = int(self.headers["Content-Length"])
                seen["body"] = self.rfile.read(seen["length"])
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass
        server, url = serve(Handler)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        opened = []
        real = client._OPENER.open
        with mock.patch.object(client._OPENER, "open", side_effect=lambda request, **kw: (opened.append(request), real(request, **kw))[1]):
            response = client.urllib_transport("POST", url + "/up", {"apikey": "k"}, b"x" * 300_000)
        self.assertEqual((response.status, seen["length"], len(seen["body"])), (200, 300_000, 300_000))
        self.assertTrue(hasattr(opened[0].data, "read"), "a bytes body is one sendall: the timeout would cover all of it")

    def test_a_redirect_is_not_followed_and_the_key_stays_home(self):
        hits = []

        class Target(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(dict(self.headers))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass
        target, target_url = serve(Target)

        class Redirect(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", target_url + "/stolen")
                self.end_headers()

            def log_message(self, *args):
                pass
        redirect, redirect_url = serve(Redirect)
        self.addCleanup(target.server_close)
        self.addCleanup(target.shutdown)
        self.addCleanup(redirect.server_close)
        self.addCleanup(redirect.shutdown)
        response = client.urllib_transport("GET", redirect_url + "/x", {"apikey": "secret-key", "Authorization": "Bearer secret-key"}, None)
        self.assertEqual(response.status, 302)
        self.assertEqual(hits, [])


class NetworkTests(ReplicateCase):
    def three(self):
        for name in ("a1", "a2", "a3"):
            self.net.local.artifacts[name] = artifact(name, name, updated_at=OLD)
            self.add_image(self.net.local, f"{name}.jpg")

    def test_one_network_failure_skips_one_artifact_and_the_next_run_finishes_it(self):
        self.three()
        failures = [ServerError("unreachable", "timed out")]
        self.net.remote.faults.append(lambda m, p, q: failures.pop() if failures and m == "HEAD" else None)
        code, out = self.run_main()
        self.assertEqual(code, 0, out)
        self.assertEqual(set(self.net.remote.artifacts), {"a2", "a3"})
        self.assertEqual(self.run_main()[0], 0)
        self.assertEqual(set(self.net.remote.artifacts), {"a1", "a2", "a3"})

    def test_three_failures_in_a_row_end_the_run(self):
        self.three()
        self.net.remote.faults.append(lambda m, p, q: ServerError("unreachable", "down") if m == "HEAD" else None)
        code, out = self.run_main()
        self.assertEqual(code, 2, out)
        self.assertEqual(self.net.remote.artifacts, {})

    def test_a_server_error_on_a_file_skips_the_artifact_and_is_retried(self):
        self.three()
        busy = [reply(503)]
        self.net.remote.faults.append(lambda m, p, q: busy.pop() if busy and m == "POST" and "/storage/" in p else None)
        self.assertEqual(self.run_main()[0], 0)
        self.assertEqual(set(self.net.remote.artifacts), {"a2", "a3"})
        self.assertEqual(self.run_main()[0], 0)
        self.assertEqual(set(self.net.remote.artifacts), {"a1", "a2", "a3"})
        self.assertQuiet()


class IncompleteCopyTests(ReplicateCase):
    def test_a_refused_copy_is_finished_later_without_looping(self):
        self.net.local.artifacts["a1"] = artifact("a1", "Late", updated_at=OLD)
        self.add_image(self.net.local, "a1.jpg")
        self.net.remote.buckets.discard("artifact-images")  # the bucket is missing for now
        self.assertEqual(self.run_main()[0], 0)
        self.assertEqual(self.net.remote.artifacts["a1"]["image_path"], f"{LOCAL_PUBLIC}/storage/v1/object/public/artifact-images/a1.jpg")
        self.assertQuiet()  # still refused: no write, no loop
        self.net.remote.buckets.add("artifact-images")
        self.assertEqual(self.run_main()[0], 0)
        self.assertEqual(self.net.remote.artifacts["a1"]["image_path"], f"{REMOTE}/storage/v1/object/public/artifact-images/a1.jpg")
        self.assertIn(("artifact-images", "a1.jpg"), self.net.remote.objects)
        self.assertQuiet()

    def test_a_model_that_did_not_fit_is_finished_when_room_appears(self):
        self.net.local.artifacts["a1"] = artifact("a1", "Big", updated_at=OLD, model="models-01/a1.glb")
        self.add_image(self.net.local, "a1.jpg")
        self.add_model(self.net.local, "a1.glb", 20)
        self.net.remote.pick = lambda size: reply(503, {"code": "53100"})
        self.assertEqual(self.run_main()[0], 0)
        self.assertQuiet()
        self.net.remote.pick = lambda size: reply(200, "models-01")
        self.assertEqual(self.run_main()[0], 0)
        copy = self.net.remote.artifacts["a1"]
        self.assertEqual(copy["model_url"], f"{REMOTE}/storage/v1/object/public/models-01/a1.glb")
        self.assertEqual(copy["model_file_id"], self.net.remote.model_files[("models-01", "a1.glb")]["id"])
        self.assertQuiet()


class BadValueTests(ReplicateCase):
    def test_unusable_timestamps_skip_only_that_row(self):
        for bad in ("infinity", "10000-01-01T00:00:00Z", "0001-01-01 BC", "9999-12-31T23:59:59-05:00"):
            self.net.local.sensors.append({"id": len(self.net.local.sensors) + 1, **reading(bad)})
        self.net.local.sensors.append({"id": 99, **reading("2026-11-26T10:00:00Z")})
        self.net.local.artifacts["bad"] = artifact("bad", "Bad", updated_at=OLD, created_at="infinity")
        self.net.remote.artifacts["bad"] = artifact("bad", "Bad", updated_at=OLD, created_at="infinity", base=REMOTE)
        self.net.local.artifacts["ok"] = artifact("ok", "Fine", updated_at=OLD)
        self.add_image(self.net.local, "ok.jpg")
        code, out = self.run_main()
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.net.remote.sensors), 1)
        self.assertIn("ok", self.net.remote.artifacts)


class ReplacedFileTests(ReplicateCase):
    def setup_pair(self, model_size=10):
        self.net.local.artifacts["a1"] = artifact("a1", "New name", updated_at="2026-11-25T10:00:00+00:00", model="models-01/a1.glb")
        self.net.remote.artifacts["a1"] = artifact("a1", "Old name", updated_at=OLD, base=REMOTE, model="models-01/a1.glb")
        new, old = BASE + dt.timedelta(days=3), BASE
        self.add_image(self.net.local, "a1.jpg", b"NEWPHOTO", modified=new)
        self.add_image(self.net.remote, "a1.jpg", b"old", modified=old)
        self.add_model(self.net.local, "a1.glb", model_size, fill=b"N", modified=new)
        self.add_model(self.net.remote, "a1.glb", 4, fill=b"O", modified=old)
        self.net.remote.pick = lambda size: reply(200, "models-01")

    def test_a_changed_photo_and_model_are_replaced_when_the_row_is_written(self):
        self.setup_pair()
        self.assertEqual(self.run_main()[0], 0)
        self.assertEqual(self.net.remote.objects[("artifact-images", "a1.jpg")][0], b"NEWPHOTO")
        self.assertEqual(self.net.remote.objects[("models-01", "a1.glb")][0], b"N" * 10)
        self.assertEqual(self.net.remote.model_files[("models-01", "a1.glb")]["size_bytes"], 10)
        self.assertQuiet()

    def test_same_size_different_content_is_found_by_the_etag(self):
        self.setup_pair(model_size=4)
        self.net.local.etags = self.net.remote.etags = True
        self.assertEqual(self.run_main()[0], 0)
        self.assertEqual(self.net.remote.objects[("models-01", "a1.glb")][0], b"N" * 4)


class GuardTests(ReplicateCase):
    def state(self):
        return json.loads((self.repo / "server" / ".replica_state.json").read_text())

    def test_a_deleted_reading_stays_deleted_after_a_long_pause_and_with_full(self):
        self.net.local.sensors.append({"id": 1, **reading("2026-11-26T10:00:00Z")})
        self.run_main()
        self.net.local.sensors.clear()
        later = NOW.replace(day=NOW.day) + (parse_ts("2026-12-20T00:00:00Z") - NOW)
        for args in ((), ("--full",)):
            code, out = self.run_main(*args, now=later)
            self.assertEqual(self.net.local.sensors, [], f"resurrected with {args}")
        self.assertIn("--full", out)
        self.assertIn("can come back", out)

    def test_the_guard_is_bounded_by_the_scan_window(self):
        self.net.local.sensors += [{"id": i, **reading(f"2026-11-26T{h:02d}:00:00Z", humidity=h)} for i, h in enumerate(range(0, 11), 1)]
        self.run_main()
        keys = self.state()["copied_into"]["remote"]
        self.assertEqual(len(keys), 2)  # only the readings within an hour of the watermark (09:00 and 10:00)

    def test_the_state_file_is_written_only_when_it_changed_and_synced_first(self):
        self.net.local.sensors.append({"id": 1, **reading("2026-11-26T10:00:00Z")})
        with mock.patch("os.fsync", wraps=os.fsync) as fsync:
            self.run_main()
            self.assertGreaterEqual(fsync.call_count, 1)
        path = self.repo / "server" / ".replica_state.json"
        before = path.stat().st_mtime_ns
        with mock.patch("os.replace", side_effect=AssertionError("rewritten")):
            self.assertEqual(self.run_main()[0], 0)
        self.assertEqual(path.stat().st_mtime_ns, before)


class RaceTests(ReplicateCase):
    def test_an_edit_made_on_the_target_meanwhile_is_not_overwritten(self):
        self.net.local.artifacts["a1"] = artifact("a1", "Pi edit", updated_at="2026-11-25T10:00:00+00:00")
        self.net.remote.artifacts["a1"] = artifact("a1", "old", updated_at=OLD, base=REMOTE)
        self.add_image(self.net.local, "a1.jpg")
        self.add_image(self.net.remote, "a1.jpg")

        def concurrent(table, query, values):
            if table == "artifacts":
                row = self.net.remote.artifacts["a1"]
                row.update(name="cloud edit", updated_at="2026-11-26T09:00:00+00:00")
        self.net.remote.before_patch = concurrent
        code, out = self.run_main()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.net.remote.artifacts["a1"]["name"], "cloud edit")
        self.assertIn("changed meanwhile", out)
        self.net.remote.before_patch = None
        self.run_main()  # replanned: the newer cloud edit now wins
        self.assertEqual(self.net.local.artifacts["a1"]["name"], "cloud edit")


class PagingTests(ReplicateCase):
    def test_rows_deleted_during_a_multi_page_read_do_not_shift_the_pages(self):
        self.net.local.sensors += [{"id": i, **reading(f"2026-11-26T10:{i // 60:02d}:{i % 60:02d}Z", humidity=i % 90)}
                                   for i in range(1, 1201)]
        original = self.net.local.rest_get

        def rest_get(table, query, rows):
            result = original(table, query, rows)
            if table == "sensor_data" and "or" not in query and "limit" in query and not getattr(self, "_cut", False):
                if len(json.loads(result.body)) == 1000:
                    self._cut = True
                    del self.net.local.sensors[:300]  # rows vanish between page one and page two
            return result
        self.net.local.rest_get = rest_get
        self.assertEqual(self.run_main()[0], 0)
        keys = [(r["created_at"], r["temperature"], r["humidity"]) for r in self.net.remote.sensors]
        self.assertEqual(len(keys), len(set(keys)), "duplicates")
        self.assertEqual(len(keys), 1200)


class CapTests(ReplicateCase):
    def test_a_server_error_from_the_cap_function_is_an_item_error(self):
        self.net.local.artifacts["a1"] = artifact("a1", "Cap", updated_at=OLD, model="models-01/a1.glb")
        self.add_model(self.net.local, "a1.glb", 20)
        self.net.remote.pick = lambda size: reply(500)
        self.assertEqual(self.run_main()[0], 0)
        self.assertNotIn(("models-01", "a1.glb"), self.net.remote.objects)
        self.assertEqual(self.net.remote.artifacts, {})

    def test_the_batch_the_target_names_is_tried_first(self):
        self.net.local.artifacts["a1"] = artifact("a1", "Batch", updated_at=OLD, model="models-01/a1.glb")
        self.add_image(self.net.local, "a1.jpg")
        self.add_model(self.net.local, "a1.glb", 20)
        self.net.remote.pick = lambda size: reply(200, "models-02")
        self.assertEqual(self.run_main()[0], 0)
        self.assertIn(("models-02", "a1.glb"), self.net.remote.objects)
        self.assertNotIn(("models-01", "a1.glb"), self.net.remote.objects)

    def test_a_failed_registration_keeps_the_copy(self):
        self.net.local.artifacts["a1"] = artifact("a1", "FK", updated_at=OLD, model="models-01/a1.glb")
        self.add_image(self.net.local, "a1.jpg")
        self.add_model(self.net.local, "a1.glb", 20)
        self.net.remote.model_buckets = {"models-02"}  # the foreign key of model_files refuses models-01
        code, out = self.run_main()
        self.assertEqual(code, 0, out)
        copy = self.net.remote.artifacts["a1"]
        self.assertEqual(copy["model_url"], f"{REMOTE}/storage/v1/object/public/models-01/a1.glb")
        self.assertIsNone(copy["model_file_id"])


class UnsafeLinkTests(ReplicateCase):
    def test_links_with_traversal_or_unknown_buckets_never_reach_storage(self):
        evil = ["artifact-images/../../../../../rest/v1/model_files", "evil-bucket/x.jpg", "artifact-images/a/../b.jpg",
                "models-01/%2e%2e/x.glb", "models-01/noext", "artifact-images//x.jpg", "artifact-images/%2e%2e%2f%2e%2e/x.jpg"]
        for n, link in enumerate(evil):
            self.net.remote.artifacts[f"e{n}"] = artifact(f"e{n}", "evil", updated_at=OLD, base=REMOTE,
                                                          image_path=f"{REMOTE}/storage/v1/object/public/{link}",
                                                          model_url=f"{REMOTE}/storage/v1/object/public/{link}")
        code, out = self.run_main()
        self.assertEqual(code, 0, out)
        paths = [p for server in (self.net.local, self.net.remote) for _, p in server.requests if "/storage/" in p]
        self.assertEqual(paths, [], paths)
        self.assertEqual(len(self.net.local.artifacts), len(evil))  # the rows still replicate, as plain links


class LocalAddressTests(ReplicateCase):
    def test_loopback_links_are_not_taken_for_the_public_address(self):
        (self.repo / "server" / "replica.env").write_text(f"REPLICA_URL={REMOTE}\nREPLICA_SECRET_KEY=remote-test-key\n")
        self.net.local.artifacts["l1"] = artifact("l1", "x", updated_at=OLD, base="http://127.0.0.1:54331")
        self.net.local.artifacts["l2"] = artifact("l2", "y", updated_at=OLD, base="http://localhost:54321")
        self.net.local.artifacts["l3"] = artifact("l3", "z", updated_at=OLD, base=LOCAL_PUBLIC)
        for name in ("l1", "l2", "l3"):
            self.add_image(self.net.local, f"{name}.jpg")
        self.net.remote.artifacts["r1"] = artifact("r1", "r", updated_at=OLD, base=REMOTE)
        self.add_image(self.net.remote, "r1.jpg")
        self.assertEqual(self.run_main()[0], 0)
        self.assertEqual(self.net.local.artifacts["r1"]["image_path"], f"{LOCAL_PUBLIC}/storage/v1/object/public/artifact-images/r1.jpg")


class ConfigTests(ReplicateCase):
    def test_a_missing_table_skips_only_that_part(self):
        self.net.remote.missing_tables = {"sensor_data"}
        self.net.local.sensors.append({"id": 1, **reading("2026-11-26T10:00:00Z")})
        self.net.local.artifacts["a1"] = artifact("a1", "A", updated_at=OLD)
        self.add_image(self.net.local, "a1.jpg")
        code, out = self.run_main()
        self.assertEqual(code, 0, out)
        self.assertIn("a1", self.net.remote.artifacts)
        self.assertIn("has no table sensor_data", out)

    def test_export_prefix_is_accepted(self):
        text = (self.repo / "server" / "replica.env").read_text().replace("REPLICA_URL", "export REPLICA_URL")
        (self.repo / "server" / "replica.env").write_text(text)
        self.net.local.sensors.append({"id": 1, **reading("2026-11-26T10:00:00Z")})
        self.assertEqual(self.run_main()[0], 0)
        self.assertEqual(len(self.net.remote.sensors), 1)

    def test_state_lives_in_the_systemd_state_directory(self):
        state_dir = self.repo / "var-lib"
        self.net.local.sensors.append({"id": 1, **reading("2026-11-26T10:00:00Z")})
        self.assertEqual(self.run_main(environ={"STATE_DIRECTORY": str(state_dir)})[0], 0)
        self.assertTrue((state_dir / "replica_state.json").exists())
        self.assertFalse((self.repo / "server" / ".replica_state.json").exists())

    def test_the_units_use_a_state_directory_not_the_repo(self):
        text = (SYSTEMD / "archoai-replicate.service").read_text()
        self.assertIn("StateDirectory=archoai", text)
        self.assertNotIn("ReadWritePaths", text)

    def test_sigterm_saves_the_state(self):
        self.net.local.sensors.append({"id": 1, **reading("2026-11-26T10:00:00Z")})

        def term_after_the_first_direction(method, path, query):
            if method == "GET" and path.endswith("/sensor_data") and "order" in query and self.net.remote.sensors:
                signal.raise_signal(signal.SIGTERM)
        self.net.remote.faults.append(term_after_the_first_direction)
        code, out = self.run_main()
        self.assertEqual(code, 143, out)
        state = json.loads((self.repo / "server" / ".replica_state.json").read_text())
        self.assertEqual(len(state["copied_into"]["remote"]), 1)


if __name__ == "__main__":
    unittest.main()


class BearerHeaderTests(unittest.TestCase):
    """New-format keys go to the cloud in the apikey header only (they are not JWTs)."""

    @staticmethod
    def headers_sent(base, key):
        seen = []

        def transport(method, url, headers, body):
            seen.append(dict(headers))
            return client.Response(200, {}, b"[]")
        client.Server("remote", base, key, transport).request("GET", "/rest/v1/sensor_data")
        return seen[0]

    def test_a_new_key_goes_to_the_cloud_without_a_bearer_token(self):
        for key in ("sb_secret_abc", "sb_publishable_abc"):
            headers = self.headers_sent("https://abcdefghijklmnop.supabase.co", key)
            self.assertEqual(headers["apikey"], key)
            self.assertNotIn("Authorization", headers)

    def test_the_pi_and_legacy_keys_keep_the_bearer_token(self):
        self.assertEqual(self.headers_sent("http://127.0.0.1:54331", "sb_secret_abc")["Authorization"],
                         "Bearer sb_secret_abc")
        self.assertEqual(self.headers_sent("https://abcdefghijklmnop.supabase.co", "eyJlegacy")["Authorization"],
                         "Bearer eyJlegacy")
