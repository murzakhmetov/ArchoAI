"""Tests of server/backup.py: a backup holds every row and file of the Pi, and its data.sql rebuilds
the rows. The restore check runs inside ONE transaction that is always rolled back (it empties the
tables, loads data.sql and compares the counts), so the live data is never changed.

    scripts/scripts/venv/bin/python -m unittest discover -s server/tests -p test_backup.py -v
"""
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

from test_server_api import ROOT, ServerTestCase

BACKUP = ROOT / "server" / "backup.py"


def psql(sql):
    done = subprocess.run(["docker", "exec", "-i", "supabase_db_ArchoAI", "psql", "-U", "supabase_admin",
                           "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-q", "-At", "-f", "-"],
                          input=sql, capture_output=True, text=True, timeout=600)
    if done.returncode != 0:
        raise AssertionError(done.stderr[-1500:])
    return done.stdout


class BackupTests(ServerTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            psql("select 1;")
        except (AssertionError, OSError) as exc:
            raise unittest.SkipTest(f"no access to the database container: {exc}")
        cls.tmp = tempfile.TemporaryDirectory(prefix="archoai-backup-test-")
        cls.dest = pathlib.Path(cls.tmp.name) / "backup"
        done = subprocess.run([sys.executable, str(BACKUP), "create", "--dest", str(cls.dest)],
                              capture_output=True, text=True, timeout=900)
        if done.returncode != 0:
            cls.tmp.cleanup()
            raise AssertionError(done.stdout + done.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_stored_file_is_in_the_backup_unchanged(self):
        manifest = json.loads((self.dest / "files.json").read_text())
        stored = int(psql("select count(*) from storage.objects;").strip())
        self.assertEqual(len(manifest), stored)
        for entry in manifest:
            data = (self.dest / "files" / entry["bucket"] / entry["name"]).read_bytes()
            self.assertEqual((len(data), hashlib.sha256(data).hexdigest()), (entry["size"], entry["sha256"]))

    def test_data_sql_rebuilds_every_row(self):
        expected = json.loads((self.dest / "counts.json").read_text())
        script = (
            "begin;\n"
            "set local session_replication_role = replica;\n"
            "delete from auth.identities; delete from auth.users; delete from public.model_files;\n"
            "delete from public.artifacts; delete from public.sensor_data;\n"
            + (self.dest / "data.sql").read_text()
            + "\nselect row_to_json(c) from (select (select count(*) from public.artifacts) as artifacts, "
              "(select count(*) from public.sensor_data) as sensor_data, "
              "(select count(*) from public.model_files) as model_files, "
              "(select count(*) from auth.users) as users) c;\n"
            "rollback;\n")
        restored = json.loads(psql(script).strip().splitlines()[-1])
        self.assertEqual(restored, expected)

    def test_restore_refuses_a_server_that_has_data(self):
        if not any(json.loads((self.dest / "counts.json").read_text()).values()):
            self.skipTest("the server is empty")
        done = subprocess.run([sys.executable, str(BACKUP), "restore", str(self.dest)],
                              capture_output=True, text=True, timeout=120)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("already has rows", done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
