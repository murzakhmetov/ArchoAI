"""Round 3: regressions found by the re-audit. The fakes answer as the real cloud PostgREST was measured to:
pick_model_bucket answers 503 for 53100 and 500 for 54000, created_at=gt.None answers 400 / 22007, a wrong base
path answers 404 / PGRST125, a missing table 404 / PGRST205."""
import datetime as dt
import json
import pathlib
import unittest
from unittest import mock

import replicate
from replica.client import Response, Server
from replicate_fakes import BASE, LOCAL_PUBLIC, REMOTE, ReplicateCase, artifact, reading, reply

OLD = "2026-11-20T10:00:00+00:00"
NEW = "2026-11-25T10:00:00+00:00"
EARLY, LATE = BASE, BASE + dt.timedelta(days=3)


class CloudFullTests(ReplicateCase):
    """N1: the full cloud answers the cap function with a 5xx whose body code says "full"."""

    def one_model(self):
        self.net.local.artifacts["a1"] = artifact("a1", "Big", updated_at=OLD, model="models-01/a1.glb")
        self.add_image(self.net.local, "a1.jpg")
        self.add_model(self.net.local, "a1.glb", 20)

    def check(self, status, code):
        self.one_model()
        self.net.remote.pick = lambda size: reply(status, {"code": code, "message": "x"})
        run_code, out = self.run_main()
        self.assertEqual(run_code, 0, out)
        copy = self.net.remote.artifacts["a1"]
        self.assertEqual(copy["model_url"], f"{LOCAL_PUBLIC}/storage/v1/object/public/models-01/a1.glb")
        self.assertNotIn(("models-01", "a1.glb"), self.net.remote.objects)
        self.assertIn(("artifact-images", "a1.jpg"), self.net.remote.objects)
        self.assertQuiet()

    def test_storage_full_503_skips_the_model_and_copies_the_row(self):
        self.check(503, "53100")

    def test_file_too_large_500_skips_the_model_and_copies_the_row(self):
        self.check(500, "54000")

    def test_another_5xx_without_a_limit_code_skips_the_artifact_for_now(self):
        self.one_model()
        self.net.remote.pick = lambda size: reply(503, {"code": "PGRST000"})
        self.assertEqual(self.run_main()[0], 0)
        self.assertEqual(self.net.remote.artifacts, {})


class FileReplacementTests(ReplicateCase):
    """N2 and C: never destroy a file that is not known to be older; replace only after the row write."""

    def pair(self, source_modified, target_modified):
        self.net.local.artifacts["a1"] = artifact("a1", "New", updated_at=NEW)
        self.net.remote.artifacts["a1"] = artifact("a1", "Old", updated_at=OLD, base=REMOTE)
        self.add_image(self.net.local, "a1.jpg", b"SOURCE", modified=source_modified)
        self.add_image(self.net.remote, "a1.jpg", b"target-file", modified=target_modified)

    def photo(self):
        return self.net.remote.objects[("artifact-images", "a1.jpg")][0]

    def test_a_newer_target_file_is_kept_while_the_row_is_still_updated(self):
        self.pair(EARLY, LATE)
        code, out = self.run_main()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.photo(), b"target-file")
        self.assertEqual(self.net.remote.artifacts["a1"]["name"], "New")
        self.assertIn("kept", out)

    def test_a_missing_timestamp_on_either_side_keeps_the_target_file(self):
        for source, target in ((LATE, None), (None, EARLY), (None, None)):
            self.setUp()
            self.pair(source, target)
            self.assertEqual(self.run_main()[0], 0)
            self.assertEqual(self.photo(), b"target-file", (source, target))

    def test_an_older_target_file_is_replaced(self):
        self.pair(LATE, EARLY)
        self.assertEqual(self.run_main()[0], 0)
        self.assertEqual(self.photo(), b"SOURCE")

    def test_the_file_is_not_replaced_when_the_row_write_did_not_happen(self):
        self.pair(LATE, EARLY)

        def concurrent(table, query, values):
            if table == "artifacts":
                self.net.remote.artifacts["a1"].update(name="cloud edit", updated_at="2026-11-26T09:00:00+00:00")
        self.net.remote.before_patch = concurrent
        self.assertEqual(self.run_main()[0], 0)
        self.assertEqual(self.photo(), b"target-file")

    def test_opaque_etags_say_nothing_about_the_content(self):
        self.net.local.artifacts["a1"] = artifact("a1", "New", updated_at=NEW)
        self.net.remote.artifacts["a1"] = artifact("a1", "Old", updated_at=OLD, base=REMOTE)
        self.add_image(self.net.local, "a1.jpg", b"same-bytes", modified=LATE)
        self.add_image(self.net.remote, "a1.jpg", b"same-bytes", modified=EARLY)
        self.net.local.etags, self.net.remote.etags = True, "opaque"  # md5 on one side, something else on the other
        before = len([w for w in self.net.remote.writes if "object" in w[1]])
        self.assertEqual(self.run_main()[0], 0)
        self.assertEqual(len([w for w in self.net.remote.writes if "object" in w[1]]), before)

    def test_two_md5_etags_that_differ_mean_a_different_file(self):
        self.pair(LATE, EARLY)
        self.net.remote.objects[("artifact-images", "a1.jpg")] = (b"TARGET", b"x" and "image/jpeg")
        self.net.local.etags = self.net.remote.etags = True  # same size (6), different md5
        self.assertEqual(self.run_main()[0], 0)
        self.assertEqual(self.photo(), b"SOURCE")


class NullTimestampTests(ReplicateCase):
    """N3: created_at is nullable and NULLs sort last."""

    def test_a_null_created_at_does_not_break_the_scan(self):
        self.net.local.sensors += [{"id": 1, **reading("2026-11-26T10:00:00.016276+00:00")}, {"id": 2, **reading(None, 30)}]
        seen = []
        for server in (self.net.local, self.net.remote):
            server.faults.append(lambda m, p, q: seen.append(q) if m == "GET" and p.endswith("/sensor_data") and "order" in q else None)
        code, out = self.run_main()
        self.assertEqual(code, 0, out)
        self.assertTrue(seen and all("not.is.null" in q["created_at"] for q in seen), seen)
        self.assertEqual(len(self.net.remote.sensors), 1)
        self.assertFalse([p for p in self.net.local.requests if "gt.None" in p[1]])

    def test_paging_stops_at_a_null_key(self):
        calls = []

        def transport(method, url, headers, body):
            calls.append(url)
            return Response(200, {}, json.dumps([{"id": 1, "created_at": None}]).encode())
        server = Server("local", "http://x", "k", transport)
        self.assertEqual(len(server.select("sensor_data", {"order": "created_at.asc,id.asc"}, keys=("created_at", "id"))), 1)
        self.assertEqual(len(calls), 1)


class StateSharingTests(ReplicateCase):
    """N4: one state and one lock for the service and for manual runs."""

    def test_a_manual_run_uses_the_service_state_directory_when_it_is_writable(self):
        shared = self.repo / "var-lib-archoai"
        shared.mkdir()
        with mock.patch.object(replicate, "SERVICE_STATE_DIR", shared):
            manual = replicate.load_config(self.repo, {}).state_file
            service = replicate.load_config(self.repo, {"STATE_DIRECTORY": str(shared)}).state_file
        self.assertEqual((manual, manual.parent), (service, shared))

    def test_without_the_service_directory_server_is_used(self):
        self.assertEqual(replicate.load_config(self.repo, {}).state_file, self.repo / "server" / ".replica_state.json")

    def test_the_lock_sits_next_to_the_shared_state(self):
        shared = self.repo / "var-lib-archoai"
        shared.mkdir()
        locks = []
        real = replicate.single_run

        def spy(path):
            locks.append(pathlib.Path(path))
            return real(path)
        with mock.patch.object(replicate, "SERVICE_STATE_DIR", shared), mock.patch.object(replicate, "single_run", spy):
            self.run_main()
            self.run_main(environ={"STATE_DIRECTORY": str(shared)})
        self.assertEqual(locks[0], locks[1])
        self.assertEqual(locks[0].parent, shared)


class WrongAddressTests(ReplicateCase):
    """N5: a wrong address is a configuration error, a missing table is not (unless both are)."""

    def test_invalid_path_404_is_a_configuration_error(self):
        self.net.remote.faults.append(lambda m, p, q: reply(404, {"code": "PGRST125", "message": "Invalid path specified in request URL"}))
        code, out = self.run_main()
        self.assertEqual(code, 2)
        self.assertIn("check REPLICA_URL", out)

    def test_a_404_that_is_not_postgrest_is_a_configuration_error(self):
        self.net.remote.faults.append(lambda m, p, q: reply(404, body=b"<html>not found</html>"))
        code, out = self.run_main()
        self.assertEqual(code, 2)
        self.assertIn("check REPLICA_URL", out)

    def test_both_tables_missing_is_a_configuration_error(self):
        self.net.remote.missing_tables = {"artifacts", "sensor_data"}
        code, out = self.run_main()
        self.assertEqual(code, 2)
        self.assertIn("check REPLICA_URL", out)

    def test_one_missing_table_still_only_skips_that_part(self):
        self.net.remote.missing_tables = {"sensor_data"}
        self.assertEqual(self.run_main()[0], 0)


class PoisonTests(ReplicateCase):
    """A reading the target refuses for good must not freeze the watermark."""

    def setUp(self):
        super().setUp()
        self.net.remote.reject_sensor = lambda row: row["humidity"] > 100
        self.net.local.sensors += [{"id": 1, **reading("2026-11-26T10:00:00Z", humidity=40)},
                                   {"id": 2, **reading("2026-11-26T10:01:00Z", humidity=150)},
                                   {"id": 3, **reading("2026-11-26T10:02:00Z", humidity=41)}]

    def state(self):
        return json.loads((self.repo / "server" / ".replica_state.json").read_text())

    def test_a_refused_reading_is_skipped_and_the_watermark_moves_on(self):
        code, out = self.run_main()
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(r["humidity"] for r in self.net.remote.sensors), [40, 41])
        self.assertEqual(out.count("refused for good"), 1)
        self.assertEqual(self.state()["watermark"]["local->remote"], "2026-11-26T10:02:00.000000Z")
        self.assertEqual(len(self.state()["poison"]["remote"]), 1)
        posts = len([w for w in self.net.remote.writes if w == ("POST", "sensor_data")])
        code, out = self.run_main()
        self.assertEqual(len([w for w in self.net.remote.writes if w == ("POST", "sensor_data")]), posts, "retried")
        self.assertNotIn("refused for good", out)

    def test_a_server_error_is_not_poison(self):
        self.net.remote.reject_sensor = None
        self.net.remote.faults.append(lambda m, p, q: reply(503) if m == "POST" and p.endswith("/sensor_data") else None)
        self.assertEqual(self.run_main()[0], 0)
        self.assertNotIn("local->remote", self.state().get("watermark", {}))
        self.assertEqual(self.state().get("poison", {}).get("remote", []), [])


class SoilColumnsTests(ReplicateCase):
    """Soil moisture, soil temperature and air ppm (migration 20261128000003) travel with a reading."""

    def test_the_new_values_are_copied(self):
        self.net.local.sensors.append({"id": 1, **reading("2026-11-26T10:00:00Z"),
                                       "soil_moisture": 37.5, "soil_temperature": 18.25, "air_ppm": 612.0})
        code, out = self.run_main()
        self.assertEqual(code, 0, out)
        [row] = self.net.remote.sensors
        self.assertEqual((row["soil_moisture"], row["soil_temperature"], row["air_ppm"]), (37.5, 18.25, 612.0))

    def test_a_server_without_the_new_columns_still_gets_the_reading_once(self):
        self.net.remote.no_soil_columns = True
        self.net.local.sensors.append({"id": 1, **reading("2026-11-26T10:00:00Z"),
                                       "soil_moisture": 37.5, "soil_temperature": 18.25, "air_ppm": 612.0})
        for _ in range(2):
            code, out = self.run_main()
            self.assertEqual(code, 0, out)
        self.assertEqual(len(self.net.remote.sensors), 1)
        self.assertNotIn("soil_moisture", self.net.remote.sensors[0])
        self.assertNotIn("refused for good", out)

    def test_old_rows_without_the_new_values_are_copied(self):
        self.net.local.sensors.append({"id": 1, **reading("2026-11-26T10:00:00Z")})
        code, out = self.run_main()
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.net.remote.sensors), 1)


class NetworkSkipTests(ReplicateCase):
    def test_many_artifacts_skipped_for_network_reasons_fail_the_run(self):
        for n in range(12):
            self.net.local.artifacts[f"a{n:02d}"] = artifact(f"a{n:02d}", "x", updated_at=OLD)
            self.add_image(self.net.local, f"a{n:02d}.jpg")
        heads = []

        def flaky(method, path, query):
            if method == "HEAD":
                heads.append(path)
                if len(heads) % 2:
                    from replica.client import ServerError
                    return ServerError("unreachable", "timed out")
            return None
        self.net.remote.faults.append(flaky)  # every other request fails: never 3 in a row
        code, out = self.run_main()
        self.assertEqual(code, 2, out)
        self.assertGreater(len(self.net.remote.artifacts), 0)


if __name__ == "__main__":
    unittest.main()
