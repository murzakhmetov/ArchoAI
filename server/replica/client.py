"""HTTP transport and a small PostgREST / Storage client. The transport is injectable (tests)."""
import email.utils
import http.client
import io
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import NamedTuple

from .util import log as default_log

FATAL = ("unreachable", "auth", "config")  # these end the run; every other error only skips one item
NETWORK_STREAK = 3               # consecutive network failures that end the artifacts phase
TABLES = ("artifacts", "sensor_data")


class ServerError(Exception):
    def __init__(self, kind, message, status=None):
        super().__init__(message)
        self.kind = kind  # "unreachable" | "auth" | "config" | "http" | "item"
        self.status = status  # HTTP status of an "http" error


class Info(NamedTuple):
    size: int
    etag: object = None
    modified: object = None  # datetime of the Last-Modified header, None when absent or unreadable


def _http_date(value):
    try:
        return email.utils.parsedate_to_datetime(value) if value else None
    except (TypeError, ValueError):
        return None


class Response(NamedTuple):
    status: int
    headers: dict
    body: bytes

    def json(self):
        try:
            return json.loads(self.body or b"null")
        except ValueError as err:
            raise ServerError("http", f"answer is not JSON: {self.body[:60]!r}") from err


def _lower(headers):
    return {key.lower(): value for key, value in headers.items()}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is an answer, not something to follow: the keys must not travel to another host."""

    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def is_new_api_key(key):
    return key.startswith(("sb_publishable_", "sb_secret_"))


def is_cloud_url(url):
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host.endswith(".supabase.co") or host.endswith(".supabase.in")


def urllib_transport(method, url, headers, body, timeout=120):
    """The body goes out as a file-like object with an explicit Content-Length: http.client then sends
    it block by block, so `timeout` applies to each block and not to the whole upload."""
    data = None
    if body is not None:
        headers = {**headers, "Content-Length": str(len(body))}
        data = io.BytesIO(body)
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return Response(response.status, _lower(response.headers), response.read())
    except urllib.error.HTTPError as err:
        return Response(err.code, _lower(err.headers), err.read())
    except (urllib.error.URLError, OSError, ssl.SSLError, http.client.HTTPException) as err:
        raise ServerError("unreachable", f"{method} {url.split('?', 1)[0]}: {err}") from err


class Server:
    """One server (the Pi or the cloud). In a dry run the write methods only count and log."""

    def __init__(self, name, base_url, key, transport, *, dry_run=False, log=default_log):
        self.name = name
        self.base = base_url.rstrip("/")
        self._key = key
        # New-format keys (sb_publishable_/sb_secret_) are not JWTs. The cloud gateway takes them in
        # the apikey header alone, and supabase-dart offers to never send them as a Bearer token.
        # The Pi's stack is known to work with both headers (backup.py, the ESP32), so it keeps them.
        self._bearer = not (is_new_api_key(key) and is_cloud_url(self.base))
        self.transport = transport
        self.dry_run = dry_run
        self.log = log
        self.writes = 0          # write requests done (planned, in a dry run)
        self.missing = set()     # tables this server does not have
        self.tolerant = False    # True: a network failure is an item error until NETWORK_STREAK in a row
        self.streak = 0
        self.net_skips = 0       # item errors caused by network failures (this run)

    def request(self, method, path, *, params=None, headers=None, body=None):
        url = self.base + path + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
        auth = {"Authorization": f"Bearer {self._key}"} if self._bearer else {}
        head = {"apikey": self._key, **auth, **(headers or {})}
        try:
            response = self.transport(method, url, head, body)
        except ServerError as err:
            if err.kind != "unreachable" or not self.tolerant:
                raise
            self.streak += 1
            if self.streak >= NETWORK_STREAK:
                raise
            self.net_skips += 1
            raise ServerError("item", str(err)) from err
        self.streak = 0
        if response.status == 401:
            raise ServerError("auth", f"{self.name}: the server refused the key (HTTP 401)")
        return response

    def _ok(self, response, what):
        if response.status >= 300:
            detail = response.body[:200].decode("utf-8", "replace")
            raise ServerError("http", f"{self.name}: {what}: HTTP {response.status} {detail}", response.status)
        return response

    def _transient(self, response, what):
        """A server hiccup (5xx, 408, 429) is an item error: nothing is written, the next run retries."""
        if response.status >= 500 or response.status in (408, 429):
            raise ServerError("item", f"{self.name}: {what}: HTTP {response.status}")
        return response

    def check(self):
        """Fail early: kind unreachable / auth / config (wrong address). Remembers tables the server lacks."""
        for table in TABLES:
            response = self.request("GET", f"/rest/v1/{table}", params={"select": "id", "limit": "1"})
            if response.status == 403:
                raise ServerError("auth", f"{self.name}: the key may not read {table} (HTTP 403)")
            if response.status == 404:
                self._missing_table(table, response)
            elif response.status >= 300:
                raise ServerError("unreachable", f"{self.name}: {table} answered HTTP {response.status}")
        if len(self.missing) == len(TABLES):
            raise ServerError("config", f"{self.name}: no table of ArchoAI found: check REPLICA_URL and the migrations")

    def _missing_table(self, table, response):
        text = response.body[:300].decode("utf-8", "replace")
        if "PGRST205" in text or "42P01" in text:
            self.missing.add(table)
            self.log(f"{self.name} has no table {table}: that part is skipped")
        else:  # PGRST125 (invalid path) or an answer that is not PostgREST's: the address is wrong
            raise ServerError("config", f"{self.name}: {table} answered 404 without a table error: check REPLICA_URL ({text[:80]})")

    def fetch(self, table, params):
        """One GET (no paging)."""
        response = self.request("GET", f"/rest/v1/{table}", params=params, headers={"Accept": "application/json"})
        return self._ok(response, f"read {table}").json()

    @staticmethod
    def _after(keys, last):
        if keys == ("id",):
            return {"id": f"gt.{last['id']}"}
        stamp, ident = last[keys[0]], last["id"]
        return {"or": f"({keys[0]}.gt.{stamp},and({keys[0]}.eq.{stamp},id.gt.{ident}))"}

    def select(self, table, params=None, keys=("id",), page=1000):
        """All rows in `order`, read by keyset (rows after the last one seen), so rows deleted or added
        meanwhile cannot shift a page. keys: ("id",) or (timestamp column, "id")."""
        rows, last = [], None
        while True:
            if last is not None and keys != ("id",) and last.get(keys[0]) is None:
                return rows  # NULLs sort last: nothing can follow
            query = {**(params or {}), "limit": str(page), **(self._after(keys, last) if last else {})}
            batch = self.fetch(table, query)
            if not batch:
                return rows
            rows.extend(batch)
            last = batch[-1]

    def _planned(self, text, result=None):
        self.writes += 1
        self.log(f"[dry-run] {self.name}: would {text}")
        return result

    def insert(self, table, rows, *, upsert_on=None, returning=False):
        if self.dry_run:
            return self._planned(f"insert {len(rows)} row(s) into {table}",
                                 [dict(row, id=row.get("id", "dry-run")) for row in rows])
        prefer = "return=representation" if returning else "return=minimal"
        params = {}
        if upsert_on:
            params["on_conflict"] = upsert_on
            prefer = "resolution=merge-duplicates," + prefer
        response = self.request("POST", f"/rest/v1/{table}", params=params, body=json.dumps(rows).encode(),
                                headers={"Content-Type": "application/json", "Prefer": prefer})
        self._transient(response, f"write {table}")
        self._ok(response, f"write {table}")
        self.writes += 1
        return response.json() if returning else None

    def patch(self, table, match, values):
        """Updates the rows that match; False when none did (a precondition in `match` no longer holds)."""
        if self.dry_run:
            self._planned(f"update {table} {match}: {sorted(values)}")
            return True
        params = {key: f"eq.{value}" for key, value in match.items()}
        response = self.request("PATCH", f"/rest/v1/{table}", params=params, body=json.dumps(values).encode(),
                                headers={"Content-Type": "application/json", "Prefer": "return=representation"})
        self._transient(response, f"update {table}")
        if not self._ok(response, f"update {table}").json():
            return False
        self.writes += 1
        return True

    def rpc(self, name, args):
        """Read-only function call (it runs in a dry run too)."""
        return self.request("POST", f"/rest/v1/rpc/{name}", body=json.dumps(args).encode(),
                            headers={"Content-Type": "application/json"})  # the caller reads error codes itself

    @staticmethod
    def _object_path(bucket, path, public=True):
        return (f"/storage/v1/object/{'public/' if public else ''}"
                f"{urllib.parse.quote(bucket, safe='')}/{urllib.parse.quote(path, safe='/')}")

    def object_info(self, bucket, path):
        """Info(size, etag) of a stored file, None when there is none."""
        response = self._transient(self.request("HEAD", self._object_path(bucket, path)), f"look up {bucket}/{path}")
        if response.status != 200:
            return None
        return Info(int(response.headers.get("content-length") or 0), response.headers.get("etag"),
                    _http_date(response.headers.get("last-modified")))

    def download(self, bucket, path):
        response = self._transient(self.request("GET", self._object_path(bucket, path)), f"read {bucket}/{path}")
        if response.status != 200:
            return None
        return response.body, response.headers.get("content-type") or "application/octet-stream"

    def upload(self, bucket, path, data, content_type, upsert=False):
        """Returns "ok", "exists" or "refused" (quota, missing bucket, type not allowed, ...)."""
        if self.dry_run:
            return self._planned(f"upload {bucket}/{path} ({len(data)} bytes)", "ok")
        response = self.request("POST", self._object_path(bucket, path, public=False), body=data,
                                headers={"Content-Type": content_type, "x-upsert": "true" if upsert else "false"})
        self._transient(response, f"upload {bucket}/{path}")
        if response.status in (200, 201):
            self.writes += 1
            return "ok"
        if response.status == 409 or b"Duplicate" in response.body:
            return "exists"
        self.log(f"{self.name}: upload {bucket}/{path} refused: HTTP {response.status} "
                 f"{response.body[:120].decode('utf-8', 'replace')}")
        return "refused"
