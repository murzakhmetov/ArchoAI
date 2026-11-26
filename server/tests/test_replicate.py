"""Tests of server/replicate.py against in-memory fakes of PostgREST and Storage (no network).
The fakes are in replicate_fakes.py; defects found by the audit are in test_replicate_audit.py.

    python -B -m unittest discover -s server/tests -p "test_replicate*.py" -v
"""
import contextlib
import io
import json
import sys
import types
import unittest
from unittest import mock

from replicate_fakes import (LOCAL_PUBLIC, NOW, REMOTE, SECRET, ReplicateCase, artifact, reading, reply)  # noqa: F401
from replica.client import ServerError
from replica.util import parse_ts
import replicate


class InertTests(ReplicateCase):
    def test_without_config_file_nothing_happens(self):
        (self.repo / "server" / "replica.env").unlink()
        self.net.down = True  # any request would raise
        code, out = self.run_main()
        self.assertEqual((code, out.strip()), (0, "replication not configured (server/replica.env)"))

    def test_empty_value_is_not_configured(self):
        self.configure(REPLICA_SECRET_KEY="")
        self.net.down = True
        self.assertEqual(self.run_main()[0], 0)


class SensorTests(ReplicateCase):
    def test_rows_are_copied_both_ways_without_duplicates(self):
        self.net.local.sensors += [{"id": 7, **reading("2026-11-26T10:00:00+00:00")}, {"id": 8, **reading("2026-11-26T10:05:00.5+00:00", 22)}]
        self.net.remote.sensors += [{"id": 3, **reading("2026-11-26T12:05:00.500000+02:00", 22)}, {"id": 4, **reading("2026-11-26T10:10:00Z", 23)}]
        code, out = self.run_main()
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.net.local.sensors), 3)
        self.assertEqual(len(self.net.remote.sensors), 3)
        self.assertEqual({r["temperature"] for r in self.net.local.sensors}, {21.0, 22, 23})
        copied = [r for r in self.net.remote.sensors if r["temperature"] == 21.0][0]
        self.assertEqual(parse_ts(copied["created_at"]), parse_ts("2026-11-26T10:00:00Z"))  # source time kept
        self.assertNotIn(copied["id"], (7, 8))  # the target assigned its own id

    def test_second_run_writes_nothing(self):
        self.net.local.sensors.append({"id": 1, **reading("2026-11-26T10:00:00+00:00")})
        self.net.remote.sensors.append({"id": 1, **reading("2026-11-26T10:01:00+00:00")})
        self.assertEqual(self.run_main()[0], 0)
        self.assertQuiet()
        self.assertEqual(self.sensor_keys(self.net.local)[0][1:], (21.0, 40.0, 500))

    def test_end_to_end_test_readings_are_skipped(self):
        self.net.local.sensors.append({"id": 1, **reading("2026-11-26T10:00:00Z", 21.5, 33.25, 3999)})
        self.net.remote.sensors.append({"id": 1, **reading("2026-11-26T10:01:00Z", 21.5, 33.25, 3999)})
        self.assertEqual(self.run_main()[0], 0)
        self.assertEqual((len(self.net.local.sensors), len(self.net.remote.sensors)), (1, 1))

    def test_copied_reading_deleted_on_its_origin_is_not_brought_back(self):
        self.net.local.sensors.append({"id": 1, **reading("2026-11-26T10:00:00Z")})
        self.run_main()
        self.assertEqual(len(self.net.remote.sensors), 1)
        self.net.local.sensors.clear()  # deleted where it was written
        for args in ((), ("--full",)):
            self.run_main(*args)
            self.assertEqual(self.net.local.sensors, [], f"resurrected with {args}")

    def test_more_than_one_page_is_read(self):
        self.net.local.sensors += [{"id": i, **reading(f"2026-11-26T10:{i // 60:02d}:{i % 60:02d}Z", humidity=i % 90)}
                                   for i in range(1, 1201)]
        self.assertEqual(self.run_main()[0], 0)
        self.assertEqual(len(self.net.remote.sensors), 1200)
        self.assertQuiet()

    def test_watermark_is_kept_and_rows_after_it_follow(self):
        self.net.local.sensors.append({"id": 1, **reading("2026-11-26T10:00:00Z")})
        self.run_main()
        state = json.loads((self.repo / "server" / ".replica_state.json").read_text())
        self.assertEqual(state["watermark"]["local->remote"], "2026-11-26T10:00:00.000000Z")
        self.net.local.sensors.append({"id": 2, **reading("2026-11-26T10:30:00Z", 25)})
        self.run_main()
        self.assertEqual(len(self.net.remote.sensors), 2)


class ArtifactTests(ReplicateCase):
    def test_missing_artifact_arrives_with_files_links_and_model_row(self):
        art = artifact("a1", "Amphora", updated_at="2026-11-20T10:00:00+00:00", model="models-01/a1.glb", model_file_id="mf-local")
        self.net.local.artifacts["a1"] = art
        self.net.local.objects[("artifact-images", "a1.jpg")] = (b"JPEG", "image/jpeg")
        self.add_model(self.net.local, "a1.glb", 12)
        self.net.local.model_files[("models-01", "a1.glb")] = {"id": "mf-local", "bucket_id": "models-01", "object_path": "a1.glb",
                                                               "source": "tripo", "tripo_task_id": "task-9", "size_bytes": 12}
        self.net.remote.pick = lambda size: reply(200, "models-01")
        code, out = self.run_main()
        self.assertEqual(code, 0, out)
        copy = self.net.remote.artifacts["a1"]
        self.assertEqual(copy["image_path"], f"{REMOTE}/storage/v1/object/public/artifact-images/a1.jpg")
        self.assertEqual(copy["model_url"], f"{REMOTE}/storage/v1/object/public/models-01/a1.glb")
        self.assertEqual((copy["created_at"], copy["updated_at"], copy["name"]), (art["created_at"], art["updated_at"], "Amphora"))
        self.assertIsNone(copy["local_model_path"])
        self.assertEqual(self.net.remote.objects[("artifact-images", "a1.jpg")][0], b"JPEG")
        self.assertEqual(self.net.remote.objects[("models-01", "a1.glb")][0], b"G" * 12)
        model = self.net.remote.model_files[("models-01", "a1.glb")]
        self.assertEqual((model["artifact_id"], model["source"], model["tripo_task_id"], model["size_bytes"]), ("a1", "tripo", "task-9", 12))
        self.assertEqual(copy["model_file_id"], model["id"])
        self.assertQuiet()

    def test_remote_artifact_is_copied_to_the_pi_with_pi_links(self):
        self.net.remote.artifacts["r1"] = artifact("r1", "Coin", updated_at="2026-11-20T10:00:00+00:00", base=REMOTE)
        self.net.remote.objects[("artifact-images", "r1.jpg")] = (b"PNG", "image/jpeg")
        self.assertEqual(self.run_main()[0], 0)
        self.assertEqual(self.net.local.artifacts["r1"]["image_path"], f"{LOCAL_PUBLIC}/storage/v1/object/public/artifact-images/r1.jpg")
        self.assertIn(("artifact-images", "r1.jpg"), self.net.local.objects)

    def test_newer_updated_at_wins_in_both_directions(self):
        old, new = "2026-11-20T10:00:00+00:00", "2026-11-25T10:00:00+00:00"
        self.net.local.artifacts["a1"] = artifact("a1", "old name", updated_at=old)
        self.net.remote.artifacts["a1"] = artifact("a1", "new name", updated_at=new, base=REMOTE)
        self.net.local.artifacts["a2"] = artifact("a2", "fresh", updated_at=new, crack_percentage=9.0)
        self.net.remote.artifacts["a2"] = artifact("a2", "fresh", updated_at=old, base=REMOTE, crack_percentage=2.0)
        for art in (self.net.local.artifacts["a1"], self.net.remote.artifacts["a2"]):
            art["local_model_path"] = "/keep/me.glb"
        self.net.local.objects[("artifact-images", "a2.jpg")] = (b"x", "image/jpeg")
        self.assertEqual(self.run_main()[0], 0)
        self.assertEqual(self.net.local.artifacts["a1"]["name"], "new name")
        self.assertEqual(self.net.remote.artifacts["a2"]["crack_percentage"], 9.0)
        self.assertEqual(self.net.local.artifacts["a1"]["local_model_path"], "/keep/me.glb")  # device specific
        self.assertEqual(self.net.remote.artifacts["a1"]["name"], "new name")
        self.assertQuiet()  # the trigger bumped updated_at; equal content must not loop

    def test_equal_content_is_not_written_whatever_updated_at_says(self):
        self.net.local.artifacts["a1"] = artifact("a1", "Same", updated_at="2026-11-01T10:00:00+00:00",
                                                  model="models-01/a1.glb", model_file_id="x")
        self.net.remote.artifacts["a1"] = artifact("a1", "Same", updated_at="2026-11-29T10:00:00+00:00", base=REMOTE,
                                                   model="models-02/a1.glb", created_at="2026-11-01T10:00:00+02:00")
        self.net.remote.artifacts["a1"]["created_at"] = "2026-11-01T08:00:00.000000+00:00"
        self.net.remote.artifacts["a1"]["local_model_path"] = None
        code, out = self.run_main()
        self.assertEqual((code, self.net.writes()), (0, 0), out)

    def test_target_refusing_the_model_still_gets_the_artifact(self):
        self.net.local.artifacts["a1"] = artifact("a1", "Big", updated_at="2026-11-20T10:00:00+00:00", model="models-01/a1.glb")
        self.net.local.objects[("artifact-images", "a1.jpg")] = (b"J", "image/jpeg")
        self.add_model(self.net.local, "a1.glb", 20)
        self.net.remote.refuse_buckets = {"models-01", "models-02"}
        self.net.remote.pick = lambda size: reply(503, {"code": "53100", "message": "storage_full"})
        code, out = self.run_main()
        self.assertEqual(code, 0, out)
        copy = self.net.remote.artifacts["a1"]
        self.assertEqual(copy["model_url"], f"{LOCAL_PUBLIC}/storage/v1/object/public/models-01/a1.glb")
        self.assertIsNone(copy["model_file_id"])
        self.assertEqual(self.net.remote.model_files, {})
        self.assertIn(("artifact-images", "a1.jpg"), self.net.remote.objects)
        self.assertQuiet()

    def test_target_naming_another_batch_gets_the_model_there(self):
        self.net.local.artifacts["a1"] = artifact("a1", "Mid", updated_at="2026-11-20T10:00:00+00:00", model="models-01/a1.glb")
        self.net.local.objects[("artifact-images", "a1.jpg")] = (b"J", "image/jpeg")
        self.add_model(self.net.local, "a1.glb", 20)
        self.net.remote.refuse_buckets = {"models-01"}
        self.net.remote.pick = lambda size: reply(200, "models-02")
        self.assertEqual(self.run_main()[0], 0)
        self.assertEqual(self.net.remote.artifacts["a1"]["model_url"], f"{REMOTE}/storage/v1/object/public/models-02/a1.glb")
        self.assertIn(("models-02", "a1.glb"), self.net.remote.model_files)
        self.assertQuiet()  # another batch is not a difference

    def test_a_pick_function_that_cannot_be_called_is_retried_not_skipped_past_the_cap(self):
        self.net.local.artifacts["a1"] = artifact("a1", "Grant", updated_at="2026-11-20T10:00:00+00:00", model="models-01/a1.glb")
        self.add_image(self.net.local, "a1.jpg")
        self.add_model(self.net.local, "a1.glb", 20)
        self.net.remote.pick = lambda size: reply(403, {"code": "42501", "message": "permission denied"})
        code, out = self.run_main()
        self.assertEqual(code, 0, out)
        self.assertNotIn(("models-01", "a1.glb"), self.net.remote.objects)  # no upload without a cap check
        self.assertNotIn("a1", self.net.remote.artifacts)  # left for the next run
        self.net.remote.pick = lambda size: reply(404, {"code": "PGRST202"})  # no such function: nothing to ask
        self.assertEqual(self.run_main()[0], 0)
        self.assertIn(("models-01", "a1.glb"), self.net.remote.objects)

    def test_a_failed_run_still_saves_the_readings_it_copied(self):
        self.net.local.sensors.append({"id": 1, **reading("2026-11-26T10:00:00Z")})
        real = self.net.remote.rest_post

        def then_down(table, query, rows):
            result = real(table, query, rows)
            self.net.down = True  # the link breaks right after the first copy
            return result
        self.net.remote.rest_post = then_down
        self.assertEqual(self.run_main()[0], 2)
        state = json.loads((self.repo / "server" / ".replica_state.json").read_text())
        self.assertEqual(len(state["copied_into"]["remote"]), 1)

    def test_a_cap_stops_a_model_even_when_the_bucket_would_take_it(self):
        self.net.local.artifacts["a1"] = artifact("a1", "Cap", updated_at="2026-11-20T10:00:00+00:00", model="models-01/a1.glb")
        self.add_model(self.net.local, "a1.glb", 20)
        self.net.remote.pick = lambda size: reply(503, {"code": "53100"})
        self.run_main()
        self.assertNotIn(("models-01", "a1.glb"), self.net.remote.objects)

    def test_unknown_local_address_skips_the_item_not_the_run(self):
        self.configure(REPLICA_LOCAL_PUBLIC_URL="")
        (self.repo / "server" / "replica.env").write_text(
            f"REPLICA_URL={REMOTE}\nREPLICA_SECRET_KEY={SECRET['remote']}\n")
        self.net.remote.artifacts["r1"] = artifact("r1", "Coin", updated_at="2026-11-20T10:00:00+00:00", base=REMOTE)
        self.net.remote.objects[("artifact-images", "r1.jpg")] = (b"x", "image/jpeg")
        code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("REPLICA_LOCAL_PUBLIC_URL", out)
        self.assertEqual(self.net.local.artifacts, {})


class RobustnessTests(ReplicateCase):
    def test_a_changed_pi_address_does_not_make_the_servers_overwrite_each_other(self):
        self.net.local.artifacts["a1"] = artifact("a1", "Moved", updated_at="2026-11-20T10:00:00+00:00", base="http://old.lan:54321")
        self.net.local.objects[("artifact-images", "a1.jpg")] = (b"J", "image/jpeg")
        self.net.remote.artifacts["a1"] = artifact("a1", "Moved", updated_at="2026-11-20T10:00:00+00:00", base=REMOTE)
        code, out = self.run_main()
        self.assertEqual((code, self.net.writes()), (0, 0), out)

    def test_a_file_that_cannot_be_downloaded_skips_that_artifact_only(self):
        for name in ("a1", "a2"):
            self.net.local.artifacts[name] = artifact(name, name, updated_at="2026-11-20T10:00:00+00:00")
            self.net.local.objects[("artifact-images", f"{name}.jpg")] = (b"J", "image/jpeg")
        real = self.net.local.storage

        def broken(method, path, body):
            if method == "GET" and path.endswith("a1.jpg"):
                raise ServerError("unreachable", "IncompleteRead")
            return real(method, path, body)
        self.net.local.storage = broken
        code, out = self.run_main()
        self.assertEqual(code, 0, out)
        self.assertEqual(set(self.net.remote.artifacts), {"a2"})  # a1 is retried by the next run
        self.assertIn(("artifact-images", "a2.jpg"), self.net.remote.objects)
        self.assertNotIn(("artifact-images", "a1.jpg"), self.net.remote.objects)

    def test_a_byte_order_mark_in_replica_env_is_ignored(self):
        path = self.repo / "server" / "replica.env"
        path.write_bytes(chr(0xFEFF).encode("utf-8") + path.read_bytes())
        self.net.local.sensors.append({"id": 1, **reading("2026-11-26T10:00:00Z")})
        self.assertEqual(self.run_main()[0], 0)
        self.assertEqual(len(self.net.remote.sensors), 1)

    def test_a_second_run_at_the_same_time_is_skipped(self):
        class Busy(types.ModuleType):
            LOCK_EX, LOCK_NB, LOCK_UN = 2, 4, 8

            @staticmethod
            def flock(handle, flags):
                if flags & 4:
                    raise BlockingIOError("locked")
        self.net.local.sensors.append({"id": 1, **reading("2026-11-26T10:00:00Z")})
        with mock.patch.dict(sys.modules, {"fcntl": Busy("fcntl")}):
            code, out = self.run_main()
        self.assertEqual((code, self.net.writes()), (0, 0))
        self.assertIn("another replication run is active", out)


class RunTests(ReplicateCase):
    def test_dry_run_writes_nothing(self):
        self.net.local.sensors.append({"id": 1, **reading("2026-11-26T10:00:00Z")})
        self.net.remote.artifacts["r1"] = artifact("r1", "Coin", updated_at="2026-11-20T10:00:00+00:00", base=REMOTE,
                                                   model="models-01/r1.glb")
        self.net.remote.objects[("artifact-images", "r1.jpg")] = (b"x", "image/jpeg")
        self.add_model(self.net.remote, "r1.glb")
        self.net.local.pick = lambda size: reply(200, "models-01")
        code, out = self.run_main("--dry-run")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.net.writes(), 0)
        self.assertEqual(self.net.local.artifacts, {})
        self.assertFalse((self.repo / "server" / ".replica_state.json").exists())
        self.assertIn("[dry-run]", out)

    def test_unreachable_server_fails_the_run(self):
        self.net.down = True
        code, out = self.run_main()
        self.assertEqual(code, 2)
        self.assertIn("replication failed", out)

    def test_refused_key_fails_the_run_without_printing_it(self):
        self.configure(REPLICA_SECRET_KEY="wrong-key")
        code, out = self.run_main()
        self.assertEqual(code, 2)
        self.assertNotIn("wrong-key", out)

    def test_state_file_can_be_moved(self):
        self.net.local.sensors.append({"id": 1, **reading("2026-11-26T10:00:00Z")})
        target = self.repo / "elsewhere.json"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            replicate.main([], repo=self.repo, environ={"REPLICA_STATE_FILE": str(target)}, transport=self.net, now=NOW)
        self.assertTrue(target.exists())


if __name__ == "__main__":
    unittest.main()
