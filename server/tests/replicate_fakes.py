"""In-memory fakes of PostgREST and Storage for the replicator tests (no network).

The fake enforces what the real servers enforce and what a sloppy client would trip over: `select=`
projection, keyset filters, an `updated_at` trigger, a precondition match on PATCH, a foreign key and a
CHECK on model_files, upsert on storage, and per-request faults and hooks.
"""
import contextlib
import datetime as dt
import email.utils
import hashlib
import io
import json
import pathlib
import re
import sys
import tempfile
import unittest
import urllib.parse
from unittest import mock

SERVER = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))

import replicate  # noqa: E402
from replica.client import Response, ServerError  # noqa: E402
from replica.util import parse_ts  # noqa: E402

LOCAL_API = "http://127.0.0.1:54331"
REMOTE = "https://cloud.example.test"
LOCAL_PUBLIC = "http://pi.lan:54321"
NOW = dt.datetime(2026, 11, 27, 12, 0, tzinfo=dt.timezone.utc)
SECRET = {"local": "local-test-key", "remote": "remote-test-key"}
BASE = dt.datetime(2026, 11, 27, 11, 0, tzinfo=dt.timezone.utc)
MODEL_PATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,198}[.]glb$")
KEYSET = re.compile(r"^\((\w+)\.gt\.(.+),and\(\1\.eq\.(.+),id\.gt\.(.+)\)\)$")


def when(value):
    """Postgres orders infinity after every date; the fake does the same for text it cannot parse."""
    try:
        return parse_ts(value)
    except (ValueError, TypeError):
        return dt.datetime.max.replace(tzinfo=dt.timezone.utc)


def as_list(value):
    return [] if value is None else value if isinstance(value, list) else [value]


def reply(status, payload=None, headers=None, body=None):
    raw = body if body is not None else (b"" if payload is None else json.dumps(payload).encode())
    return Response(status, {"content-type": "application/json", **(headers or {})}, raw)


class FakeServer:
    """The parts of PostgREST and Storage that the replicator uses."""

    def __init__(self, key):
        self.key = key
        self.sensors, self.artifacts, self.model_files, self.objects = [], {}, {}, {}
        self.buckets = {"artifact-images", "models-01", "models-02", "3d1"}
        self.model_buckets = {"models-01", "models-02", "3d1"}
        self.refuse_buckets = set()
        self.missing_tables = set()
        self.pick = None            # None: function missing (404); else callable(size) -> Response
        self.etags = False          # HEAD answers an etag (md5 of the content)
        self.faults = []            # [predicate(method, path, query) -> Response | Exception | None]
        self.before_patch = None    # hook(table, query, values) run before a PATCH is applied
        self.requests = []          # (method, path) of every request
        self.writes = []
        self.modified = {}          # object key -> datetime (Last-Modified); absent: the header is left out
        self.reject_sensor = None   # predicate(row) -> True: a 400 / 23514 CHECK violation, as the real table gives
        self.no_soil_columns = False  # True: a server without migration 20261128000003 (PGRST204 on the new columns)
        self.ticks = 0
        self.next_id = 1

    def tick(self):
        self.ticks += 1
        return (BASE + dt.timedelta(seconds=self.ticks)).isoformat()

    def handle(self, method, path, query, body):
        self.requests.append((method, path))
        for fault in list(self.faults):
            outcome = fault(method, path, query)
            if isinstance(outcome, Exception):
                raise outcome
            if outcome is not None:
                return outcome
        if path.startswith("/rest/v1/rpc/"):
            return self.rpc(json.loads(body))
        if path.startswith("/rest/v1/"):
            table = path.split("/")[3]
            if table in self.missing_tables:
                return reply(404, {"code": "PGRST205"})
            return self.rest(method, table, query, json.loads(body) if body else None)
        return self.storage(method, path, body)

    def rest(self, method, table, query, rows):
        if method != "GET":
            self.writes.append((method, table))
        return getattr(self, f"rest_{method.lower()}")(table, query, rows)

    def collection(self, table):
        if table == "sensor_data":
            return self.sensors
        return list((self.artifacts if table == "artifacts" else self.model_files).values())

    def rest_get(self, table, query, _):
        rows = self.collection(table)
        for cond in as_list(query.get("created_at")):
            if cond == "not.is.null":
                rows = [r for r in rows if r.get("created_at") is not None]
            else:
                floor = parse_ts(cond[4:])
                rows = [r for r in rows if when(r["created_at"]) >= floor]
        if "id" in query:
            op, _, value = query["id"].partition(".")
            rows = [r for r in rows if (str(r["id"]) == value if op == "eq" else str(r["id"]) > value)]
        if "or" in query:
            col, stamp, same, ident = KEYSET.match(query["or"]).groups()
            if stamp == "None":  # what the real PostgREST says to created_at=gt.None
                return reply(400, {"code": "22007", "message": "invalid input syntax for type timestamp with time zone"})
            after = when(stamp)
            rows = [r for r in rows if when(r[col]) > after or (when(r[col]) == after and r["id"] > int(ident))]
        if query.get("order", "id.asc").startswith("created_at"):
            rows = sorted(rows, key=lambda r: (when(r["created_at"]), r["id"]))
        else:
            rows = sorted(rows, key=lambda r: str(r["id"]))
        start = int(query.get("offset", 0))
        rows = rows[start:start + int(query.get("limit", 1000))]
        columns = query.get("select", "*")
        if columns != "*":
            rows = [{name: r[name] for name in columns.split(",")} for r in rows]
        return reply(200, rows)

    def rest_post(self, table, query, rows):
        if table == "sensor_data":
            if self.no_soil_columns and any("soil_moisture" in row for row in rows):
                return reply(400, {"code": "PGRST204",
                                   "message": "Could not find the 'air_ppm' column of 'sensor_data' in the schema cache"})
            if self.reject_sensor and any(self.reject_sensor(row) for row in rows):
                return reply(400, {"code": "23514", "message": "new row violates check constraint"})
            for row in rows:
                self.sensors.append({"id": self.next_id, "created_at": self.tick(), **row})
                self.next_id += 1
            return reply(201)
        store = self.artifacts if table == "artifacts" else self.model_files
        out = []
        for row in rows:
            if table == "model_files":
                error = self.check_model_row(row)
                if error:
                    return error
                key = self.model_key(row)
                row = {**store[key], **row} if key in store else {"id": f"mf-{len(store) + 1}", "created_at": self.tick(), **row}
                store[key] = row
            else:
                if row["id"] in store:
                    return reply(409, {"code": "23505"})
                row = {"updated_at": self.tick(), **row}
                store[row["id"]] = row
            out.append(row)
        return reply(201, out)

    def check_model_row(self, row):
        if row["bucket_id"] not in self.model_buckets:
            return reply(409, {"code": "23503", "message": "foreign key"})
        if not MODEL_PATH.match(row["object_path"]):
            return reply(400, {"code": "23514", "message": "check constraint"})
        return None

    @staticmethod
    def model_key(row):
        return (row["bucket_id"], row["object_path"])

    def rest_patch(self, table, query, values):
        if self.before_patch:
            self.before_patch(table, query, values)
        rows = [r for r in self.collection(table) if str(r["id"]) == query["id"][3:]]
        if "updated_at" in query:
            rows = [r for r in rows if r["updated_at"] == query["updated_at"][3:]]
        for row in rows:
            row.update(values)
            if table == "artifacts":
                row["updated_at"] = self.tick()  # trigger: new.updated_at := now()
        return reply(200, rows)

    def rpc(self, args):
        return reply(404) if self.pick is None else self.pick(args["p_size_bytes"])

    def storage(self, method, path, body):
        public = "/public/" in path
        bucket, _, name = path.split("/storage/v1/object/", 1)[1].removeprefix("public/").partition("/")
        key = (bucket, urllib.parse.unquote(name))
        if method in ("GET", "HEAD"):
            if key not in self.objects:
                return reply(404, body=b"")
            data, kind = self.objects[key]
            headers = {"content-type": kind, "content-length": str(len(data))}
            if key in self.modified:
                headers["last-modified"] = email.utils.format_datetime(self.modified[key], usegmt=True)
            if self.etags == "opaque":  # not an md5, like a file backend that hashes size and mtime
                headers["etag"] = f'"{len(data)}-{id(self) % 9973}"'
            elif self.etags:
                headers["etag"] = '"' + hashlib.md5(data).hexdigest() + '"'
            return Response(200, headers, data if method == "GET" else b"")
        assert not public and method == "POST", path
        return self.upload(bucket, key, body)

    def upload(self, bucket, key, body):
        if bucket not in self.buckets:
            return reply(404, {"error": "Bucket not found"})
        if bucket in self.refuse_buckets:
            return reply(413, {"error": "quota"})
        if key in self.objects and not self.upsert:
            return reply(400, {"error": "Duplicate"})
        self.writes.append(("POST", f"object {bucket}/{key[1]}"))
        self.modified[key] = BASE + dt.timedelta(days=1, seconds=len(self.writes))
        self.objects[key] = (body, "model/gltf-binary" if bucket.startswith("models") else "image/jpeg")
        return reply(200, {"Key": f"{bucket}/{key[1]}"})

    upsert = False


def query_dict(text):
    """Query string as a dict; a key that repeats (created_at=...&created_at=...) maps to a list."""
    out = {}
    for key, value in urllib.parse.parse_qsl(text):
        out[key] = [*as_list(out[key]), value] if key in out else value
    return out


class FakeNet:
    def __init__(self):
        self.servers = {LOCAL_API: FakeServer(SECRET["local"]), REMOTE: FakeServer(SECRET["remote"])}
        self.local, self.remote = self.servers[LOCAL_API], self.servers[REMOTE]
        self.down = False

    def __call__(self, method, url, headers, body):
        if self.down:
            raise ServerError("unreachable", "connection refused")
        parts = urllib.parse.urlsplit(url)
        server = self.servers[f"{parts.scheme}://{parts.netloc}"]
        if headers["apikey"] != server.key:
            return reply(401, {"message": "Invalid API key"})
        server.upsert = headers.get("x-upsert") == "true"
        return server.handle(method, parts.path, query_dict(parts.query), body)

    def writes(self):
        return len(self.local.writes) + len(self.remote.writes)


def reading(created_at, temperature=21.0, humidity=40.0, air_quality=500):
    return {"created_at": created_at, "temperature": temperature, "humidity": humidity, "air_quality": air_quality}


def artifact(art_id, name, *, updated_at, base=LOCAL_PUBLIC, model=None, **extra):
    return {"id": art_id, "name": name, "image_path": f"{base}/storage/v1/object/public/artifact-images/{art_id}.jpg",
            "model_url": f"{base}/storage/v1/object/public/{model}" if model else None, "local_model_path": "/home/x/y.glb",
            "created_at": "2026-11-01T08:00:00+00:00", "status": "complete", "type": "Vase", "material": "Clay",
            "era": "Roman", "purpose": "Unknown", "condition": "Stable", "crack_percentage": 1.5,
            "updated_at": updated_at, "tripo_task_id": None, "model_file_id": None, **extra}


class ReplicateCase(unittest.TestCase):
    def setUp(self):
        self.net = FakeNet()
        patcher = mock.patch.object(replicate, "SERVICE_STATE_DIR", pathlib.Path("/nonexistent/archoai"))
        patcher.start()
        self.addCleanup(patcher.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = pathlib.Path(tmp.name)
        (self.repo / "server").mkdir()
        (self.repo / "supabase").mkdir()
        (self.repo / "supabase" / ".env").write_text(f"ARCHOAI_SECRET_KEY={SECRET['local']}\n")
        self.configure()

    def configure(self, **values):
        env = {"REPLICA_URL": REMOTE, "REPLICA_SECRET_KEY": SECRET["remote"], "REPLICA_LOCAL_PUBLIC_URL": LOCAL_PUBLIC, **values}
        text = "# test\n" + "".join(f"{k}={v}\n" for k, v in env.items())
        (self.repo / "server" / "replica.env").write_text(text)

    def run_main(self, *args, environ=None, now=NOW):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = replicate.main(list(args), repo=self.repo, environ=environ or {}, transport=self.net, now=now)
        return code, out.getvalue()

    def sensor_keys(self, server):
        return sorted((r["created_at"], r["temperature"], r["humidity"], r["air_quality"]) for r in server.sensors)

    def add_model(self, server, name, size=8, bucket="models-01", fill=b"G", modified=None):
        server.objects[(bucket, name)] = (fill * size, "model/gltf-binary")
        if modified:
            server.modified[(bucket, name)] = modified

    def add_image(self, server, name, data=b"J", modified=None):
        server.objects[("artifact-images", name)] = (data, "image/jpeg")
        if modified:
            server.modified[("artifact-images", name)] = modified

    def assertQuiet(self):
        before = self.net.writes()
        code, out = self.run_main()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.net.writes(), before, f"second run wrote something:\n{out}")
