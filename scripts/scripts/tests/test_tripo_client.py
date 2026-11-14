"""Unit tests for tripo_client.

No external network is used.  Most tests inject a scripted fake ``requests`` session and a fake
clock, so they are instant and deterministic; a few (class LoopbackTests) talk to a stdlib HTTP
server on 127.0.0.1 to check what the real requests/urllib3 stack does.  Run from scripts/scripts:

    python -m unittest discover -s tests -v
"""

import contextlib
import gc
import http.server
import io
import json
import os
import ssl
import struct
import sys
import threading
import time
import traceback
import unittest
import warnings
from unittest import mock

# Make "import tripo_client" work whatever the current directory is.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from urllib3.exceptions import MaxRetryError, NewConnectionError  # installed together with requests
from urllib3.exceptions import SSLError as Urllib3SSLError

import tripo_client
from tripo_client import TripoClient, TripoError, TripoInputError, TripoTask

API = "https://openapi.tripo3d.ai/v3"
CHINA = "https://openapi.tripo3d.com/v3"
KEY = "tsk_secret_key_123"
MODEL = "v3.1-20260211"


def make_glb(extra=52):
    """A well-formed GLB: magic, version 2 and the total length in the header."""
    return b"glTF" + struct.pack("<II", 2, 12 + extra) + b"\x00" * extra


GLB = make_glb()


# --------------------------------------------------------------------------- fakes

class FakeResponse:
    """Just enough of requests.Response for the client."""

    def __init__(self, status_code=200, payload=None, headers=None, body=b"", chunks=None,
                 text=None, not_json=False):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.content = body
        self._chunks = chunks
        self._not_json = not_json
        if text is not None:
            self.text = text
        else:
            self.text = json.dumps(payload) if payload is not None else ""
        self.closed = False
        self.iterated = False
        self.chunk_sizes = []  # the chunk_size of every iter_content() call

    def json(self):
        if self._not_json or self._payload is None:
            raise ValueError("Expecting value")
        return self._payload

    def iter_content(self, chunk_size=1):
        self.iterated = True
        self.chunk_sizes.append(chunk_size)
        if self._chunks is not None:
            for item in self._chunks:
                if isinstance(item, BaseException):
                    raise item
                yield item
            return
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start:start + chunk_size]

    def close(self):
        self.closed = True


class SlowResponse(FakeResponse):
    """A download that trickles in: every chunk costs ``seconds_per_chunk`` of fake time."""

    def __init__(self, clock, chunks, seconds_per_chunk):
        super().__init__(200, chunks=chunks)
        self._clock = clock
        self._seconds_per_chunk = seconds_per_chunk
        self.chunks_served = 0

    def iter_content(self, chunk_size=1):
        self.iterated = True
        self.chunk_sizes.append(chunk_size)
        for chunk in self._chunks:
            self._clock.now += self._seconds_per_chunk
            self.chunks_served += 1
            yield chunk


class FakeSession:
    """Plays back a script: a response is returned, an exception is raised, in call order.

    When the script is used up, ``handler(method, url, kwargs)`` decides (it may return a response
    or an exception to raise); without a handler any further call is a test failure.
    """

    def __init__(self, *script, handler=None):
        self.script = list(script)
        self.handler = handler
        self.calls = []  # (method, url, kwargs)
        self.headers = {}  # like requests.Session: the client must never put the API key here

    def _next(self, method, url, kwargs):
        self.calls.append((method, url, kwargs))
        if len(self.calls) > 5000:
            raise AssertionError("runaway request loop")
        if self.script:
            item = self.script.pop(0)
        elif self.handler is not None:
            item = self.handler(method, url, kwargs)
        else:
            raise AssertionError(f"unexpected extra request: {method} {url}")
        if isinstance(item, BaseException):
            raise item
        return item

    def post(self, url, **kwargs):
        return self._next("POST", url, kwargs)

    def get(self, url, **kwargs):
        return self._next("GET", url, kwargs)

    def urls(self):
        return [url for _, url, _ in self.calls]


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def make_client(*script, handler=None, clock=None, **kwargs):
    session = FakeSession(*script, handler=handler)
    clock = clock or FakeClock()
    client = TripoClient(KEY, session=session, sleep=clock.sleep, monotonic=clock.monotonic, **kwargs)
    return client, session, clock


def ok(data=None, **kwargs):
    return FakeResponse(200, {"code": 0, "data": data if data is not None else {}}, **kwargs)


def api_error(status, code, message="something went wrong", headers=None):
    return FakeResponse(
        status,
        {"code": code, "status": "error", "message": message,
         "suggestion": "try later", "request_id": "req_abc123"},
        headers=headers,
    )


def html_error(status):
    return FakeResponse(status, not_json=True, text="<html>Bad gateway</html>")


def task_resp(status, progress=0, **extra):
    data = {"task_id": "t1", "type": "image_to_model", "status": status, "progress": progress}
    data.update(extra)
    return ok(data)


def finished_resp(url="https://cdn.tripo3d.ai/out/model.glb"):
    return task_resp("success", 100, output={"model_url": url})


def outage_until(clock, end, outcome):
    """Session handler: every call fails (``outcome()``) until the fake clock reaches ``end``."""
    def handler(method, url, kwargs):
        return outcome() if clock.now < end else finished_resp()
    return handler


def connect_timeout():
    return requests.exceptions.ConnectTimeout("connect timed out")


def dns_failure():
    reason = NewConnectionError(None, "Failed to establish a new connection: name resolution failed")
    return requests.exceptions.ConnectionError(MaxRetryError(None, "/v3/files", reason))


def connection_refused():
    reason = NewConnectionError(None, "Failed to establish a new connection: [Errno 111] Connection refused")
    return requests.exceptions.ConnectionError(MaxRetryError(None, "/v3/files", reason))


def mid_flight_reset():
    return requests.exceptions.ConnectionError(
        "Connection aborted.", ConnectionResetError(104, "Connection reset by peer")
    )


def tls_failure(inner):
    """requests' SSLError as a real connection produces it: urllib3 and ssl errors wrapped inside."""
    return requests.exceptions.SSLError(MaxRetryError(None, "/v3/generation", Urllib3SSLError(inner)))


def tls_certificate_failure():  # can only happen while the connection is being set up
    return tls_failure(ssl.SSLCertVerificationError(
        1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate"))


def tls_handshake_alert():  # can only happen while the connection is being set up
    return tls_failure(ssl.SSLError(1, "[SSL: SSLV3_ALERT_HANDSHAKE_FAILURE] sslv3 alert handshake failure"))


def tls_eof_after_send():  # the peer cut the TLS session: the request may have been processed
    return tls_failure(ssl.SSLEOFError(8, "EOF occurred in violation of protocol (_ssl.c:2427)"))


def tls_garbage_after_send():  # not TLS data where a response was expected
    return tls_failure(ssl.SSLError(1, "[SSL: WRONG_VERSION_NUMBER] wrong version number (_ssl.c:2427)"))


# Failures after which the request may already have reached Tripo.
AMBIGUOUS_FAILURES = {
    "read timeout": lambda: requests.exceptions.ReadTimeout("read timed out"),
    "http 500": lambda: api_error(500, 1000, "Unknown error on server side"),
    "http 502 html": lambda: html_error(502),
    "http 503": lambda: api_error(503, 1001),
    "http 408 without envelope": lambda: html_error(408),
    "connection reset mid-flight": mid_flight_reset,
    "bare connection error": lambda: requests.exceptions.ConnectionError("Connection aborted."),
    "broken body": lambda: requests.exceptions.ChunkedEncodingError("connection broken"),
    "proxy error": lambda: requests.exceptions.ProxyError("proxy refused"),
    "tls eof after the request was sent": tls_eof_after_send,
    "tls garbage after the request was sent": tls_garbage_after_send,
}

# Failures that prove nothing reached Tripo.
UNREACHABLE_FAILURES = {
    "connect timeout": connect_timeout,
    "dns failure": dns_failure,
    "connection refused": connection_refused,
    "ssl error without detail": lambda: requests.exceptions.SSLError("handshake failed"),
    "tls certificate failure": tls_certificate_failure,
    "tls handshake alert": tls_handshake_alert,
    # recognised by the exception class alone: the text carries no "certificate verify failed"
    "tls hostname mismatch": lambda: tls_failure(ssl.SSLCertVerificationError(1, "Hostname mismatch")),
}


class LoopbackServer:
    """A scripted HTTP server on 127.0.0.1 (stdlib only) for checks against the real requests stack.

    ``script`` maps (method, path) to a list of (status, headers, body) answers that are used up in
    order; every request is recorded in ``seen`` as (method, path, headers, body).  A body may also
    be a callable: it receives the request handler and writes the body itself (for slow answers).
    """

    def __init__(self):
        self.seen = []
        self.script = {}
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _answer(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                outer.seen.append((self.command, self.path, dict(self.headers), body))
                queue = outer.script.get((self.command, self.path)) or []
                status, headers, payload = queue.pop(0) if queue else (500, {}, b"nothing scripted")
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                if "Content-Length" not in headers:
                    self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if callable(payload):
                    payload(self)
                else:
                    self.wfile.write(payload)

            do_GET = _answer
            do_POST = _answer

            def log_message(self, *args):  # keep the test output quiet
                pass

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()

    @property
    def port(self):
        return self.httpd.server_address[1]

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}/v3"

    def paths(self):
        return [(method, path) for method, path, _, _ in self.seen]

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(5)


# --------------------------------------------------------------------------- tests

class ConfigTests(unittest.TestCase):
    def test_empty_api_key_raises_before_any_network_call(self):
        session = FakeSession()
        for key in ("", "   ", None):
            with self.assertRaises(TripoError):
                TripoClient(key, session=session)
        self.assertEqual(session.calls, [])

    def test_malformed_api_key_is_rejected_without_leaking_it(self):
        session = FakeSession()
        for key in ("tsk_ключ", "tsk abc", "tsk\nabc", "tsk_сecret"):
            with self.subTest(key=key):
                with self.assertRaises(TripoError) as ctx:
                    TripoClient(key, session=session)
                self.assertNotIn(key, str(ctx.exception))
                self.assertTrue(ctx.exception.hint)
        self.assertEqual(session.calls, [])

    def test_defaults(self):
        self.assertEqual(TripoClient.DEFAULT_BASE_URL, API)
        self.assertEqual(TripoClient.CHINA_BASE_URL, CHINA)
        self.assertEqual(TripoClient.DEFAULT_MODEL, MODEL)
        client = TripoClient(KEY, session=FakeSession())
        self.assertEqual(client.base_url, API)
        self.assertEqual(client.model, MODEL)
        self.assertIsNone(client.face_limit)

    def test_base_url_argument(self):
        for given, expected in (("https://x.example.test/v3/", "https://x.example.test/v3"),
                                ("  https://y.example.test/v3  ", "https://y.example.test/v3"),
                                (None, API), ("", API)):
            with self.subTest(given=given):
                self.assertEqual(TripoClient(KEY, base_url=given, session=FakeSession()).base_url, expected)
        with self.assertRaises(TripoInputError):
            TripoClient(KEY, base_url=["https://a/v3", "https://b/v3"], session=FakeSession())
        with self.assertRaises(TypeError):  # the two-host argument of older versions is gone
            TripoClient(KEY, base_urls=["https://a/v3"], session=FakeSession())

    def test_from_env_reads_all_variables(self):
        env = {
            "TRIPO_API_KEY": KEY,
            "TRIPO_BASE_URL": "https://tripo.example.test/v3/",
            "TRIPO_MODEL": "v3.0-20250812",
            "TRIPO_FACE_LIMIT": "40000",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            session = FakeSession(ok({"task_id": "t1"}))
            client = TripoClient.from_env(session=session)
        self.assertEqual(client.base_url, "https://tripo.example.test/v3")  # trailing slash stripped
        self.assertEqual(client.model, "v3.0-20250812")
        self.assertEqual(client.face_limit, 40000)
        client.create_image_to_model("tok")
        self.assertEqual(session.urls(), ["https://tripo.example.test/v3/generation/image-to-model"])
        self.assertEqual(session.calls[0][2]["json"],
                         {"input": "tok", "model": "v3.0-20250812", "face_limit": 40000})

    def test_from_env_defaults_and_invalid_face_limit_ignored(self):
        for bad in ("abc", "0", "-5", "12.5x", ""):
            with self.subTest(face_limit=bad):
                with mock.patch.dict(os.environ, {"TRIPO_API_KEY": KEY, "TRIPO_FACE_LIMIT": bad}, clear=True):
                    client = TripoClient.from_env(session=FakeSession())
                self.assertIsNone(client.face_limit)
                self.assertEqual(client.base_url, API)
                self.assertEqual(client.model, MODEL)

    def test_from_env_without_key_raises(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(TripoError):
                TripoClient.from_env(session=FakeSession())

    def test_region_selects_the_host_without_a_warning(self):
        cases = {None: API, "": API, "global": API, "GLOBAL": API, "china": CHINA, "cn": CHINA,
                 " CN ": CHINA, "China": CHINA}
        for region, expected in cases.items():
            with self.subTest(region=region):
                env = {"TRIPO_API_KEY": KEY}
                if region is not None:
                    env["TRIPO_REGION"] = region
                out = io.StringIO()
                with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(out):
                    client = TripoClient.from_env(session=FakeSession())
                self.assertEqual(client.base_url, expected)
                self.assertEqual(out.getvalue(), "")

    def test_unknown_region_prints_a_warning_and_uses_global(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"TRIPO_API_KEY": KEY, "TRIPO_REGION": "Mars"}, clear=True), \
                contextlib.redirect_stdout(out):
            client = TripoClient.from_env(session=FakeSession())
        self.assertEqual(client.base_url, API)
        self.assertIn("TRIPO_REGION", out.getvalue())
        self.assertIn("mars", out.getvalue())

    def test_base_url_overrides_the_region(self):
        env = {"TRIPO_API_KEY": KEY, "TRIPO_REGION": "china", "TRIPO_BASE_URL": "https://proxy.example.test/v3"}
        with mock.patch.dict(os.environ, env, clear=True):
            client = TripoClient.from_env(session=FakeSession())
        self.assertEqual(client.base_url, "https://proxy.example.test/v3")

    def test_invalid_face_limit_argument_rejected(self):
        for bad in (0, -1, "x"):
            with self.assertRaises(TripoInputError):
                TripoClient(KEY, face_limit=bad, session=FakeSession())

    def test_base_url_must_not_contain_whitespace_or_control_characters(self):
        for bad in ("https://a.example/v3\nX-Evil: 1", "https://a.example/ v3", "https://a.example/v3\t/x",
                    "https://a.example/\x00v3"):
            with self.subTest(url=repr(bad)):
                with self.assertRaises(TripoInputError):
                    TripoClient(KEY, base_url=bad, session=FakeSession())

    def _warnings_for(self, base_url):
        with self.assertLogs(tripo_client.logger, level="WARNING") as logs:
            tripo_client.logger.warning("sentinel")  # assertLogs needs at least one record
            TripoClient(KEY, base_url=base_url, session=FakeSession())
        return [r.getMessage() for r in logs.records if r.getMessage() != "sentinel"]

    def test_plain_http_to_a_remote_host_logs_a_warning(self):
        for url in ("http://tripo.example.test/v3", "http://203.0.113.5:8080/v3"):
            with self.subTest(url=url):
                messages = self._warnings_for(url)
                self.assertEqual(len(messages), 1)
                self.assertIn("unencrypted", messages[0])
                self.assertNotIn(KEY, messages[0])

    def test_https_and_loopback_http_do_not_warn(self):
        for url in (API, CHINA, "http://localhost:8080/v3", "http://127.0.0.1:9/v3", "http://[::1]:9/v3", None):
            with self.subTest(url=url):
                self.assertEqual(self._warnings_for(url), [])


class ErrorTypeTests(unittest.TestCase):
    def test_known_codes_have_friendly_hints(self):
        for code in (2, 1002, 1000, 1001, 1003, 1004, 1007, 2000, 2001, 2004, 2008, 2010, 2015, 2018):
            with self.subTest(code=code):
                self.assertTrue(TripoError("x", code=code).hint)

    def test_str_is_readable(self):
        err = TripoError("Out of credit", code=2010, http_status=403, request_id="req_1", suggestion="Top up")
        text = str(err)
        for part in ("Out of credit", "2010", "403", "req_1", "Top up"):
            self.assertIn(part, text)

    def test_defaults(self):
        err = TripoError("x")
        self.assertIsNone(err.code)
        self.assertIsNone(err.task_error_code)
        self.assertFalse(err.transient)
        self.assertFalse(err.maybe_created)

    def test_input_error_is_both_tripo_and_value_error(self):
        err = TripoInputError("bad argument")
        self.assertIsInstance(err, TripoError)
        self.assertIsInstance(err, ValueError)

    def test_task_flags_and_lowercase_status(self):
        self.assertTrue(TripoTask("t", "SUCCESS").is_success)
        for status in ("failed", "cancelled", "banned", "expired"):
            self.assertTrue(TripoTask("t", status).is_failed)
        for status in ("queued", "running", "unknown", "success"):
            self.assertFalse(TripoTask("t", status).is_failed)
        self.assertFalse(TripoTask("t", "running").is_success)

    def test_non_string_task_status_becomes_unknown(self):
        for value in (None, 7, ["running"], {"a": 1}, "", "   ", True):
            with self.subTest(value=value):
                task = TripoTask("t", value)
                self.assertEqual(task.status, "unknown")
                self.assertFalse(task.is_success or task.is_failed)


class UploadTests(unittest.TestCase):
    def test_upload_posts_multipart_file_and_returns_token(self):
        client, session, _ = make_client(ok({"file_token": "file_abc"}))
        token = client.upload_image(b"\xff\xd8jpeg", "view_1.jpg")
        self.assertEqual(token, "file_abc")
        (method, url, kwargs), = session.calls
        self.assertEqual((method, url), ("POST", API + "/files"))
        self.assertEqual(kwargs["headers"], {"Authorization": f"Bearer {KEY}"})
        self.assertEqual(kwargs["files"], {"file": ("view_1.jpg", b"\xff\xd8jpeg", "image/jpeg")})
        self.assertNotIn("json", kwargs)
        self.assertEqual(kwargs["timeout"], (10.0, 120.0))
        self.assertIs(kwargs["allow_redirects"], False)

    def test_upload_mime_can_be_changed(self):
        client, session, _ = make_client(ok({"file_token": "f"}))
        client.upload_image(b"\x89PNG", "a.png", "image/png")
        self.assertEqual(session.calls[0][2]["files"]["file"][2], "image/png")

    def test_upload_goes_to_the_configured_base_url(self):
        client, session, _ = make_client(ok({"file_token": "f"}), base_url=CHINA)
        client.upload_image(b"x", "a.jpg")
        self.assertEqual(session.urls(), [CHINA + "/files"])

    def test_upload_is_retried_after_read_timeout(self):
        client, session, clock = make_client(requests.exceptions.ReadTimeout("slow"), ok({"file_token": "f"}))
        self.assertEqual(client.upload_image(b"x", "a.jpg"), "f")
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(clock.sleeps, [1.0])

    def test_upload_is_retried_after_every_kind_of_network_error(self):
        failures = dict(AMBIGUOUS_FAILURES)
        failures.update(UNREACHABLE_FAILURES)
        failures.pop("http 408 without envelope")  # a plain 4xx is not retried, see the next tests
        for label, failure in failures.items():
            with self.subTest(label):
                client, session, clock = make_client(failure(), ok({"file_token": "f"}))
                self.assertEqual(client.upload_image(b"x", "a.jpg"), "f")
                self.assertEqual(len(session.calls), 2)
                self.assertEqual(clock.sleeps, [1.0])
                self.assertEqual(set(session.urls()), {API + "/files"})  # always the same host

    def test_upload_gives_up_after_three_attempts(self):
        client, session, clock = make_client(api_error(503, 1000), api_error(503, 1000), api_error(503, 1000))
        with self.assertRaises(TripoError) as ctx:
            client.upload_image(b"x", "a.jpg")
        self.assertEqual(ctx.exception.http_status, 503)
        self.assertEqual(len(session.calls), 3)
        self.assertEqual(clock.sleeps, [1.0, 2.0])

    def test_upload_not_retried_on_client_error(self):
        client, session, clock = make_client(api_error(400, 2004, "This image file type is not supported"))
        with self.assertRaises(TripoError) as ctx:
            client.upload_image(b"x", "a.gif")
        self.assertEqual(ctx.exception.code, 2004)
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(clock.sleeps, [])

    def test_upload_validates_input_without_network(self):
        client, session, _ = make_client()
        with self.assertRaises(TripoInputError):
            client.upload_image(b"", "a.jpg")
        with self.assertRaises(TripoInputError):
            client.upload_image(b"x" * (20 * 1024 * 1024 + 1), "big.jpg")
        self.assertEqual(session.calls, [])

    def test_upload_without_token_in_answer_is_an_error(self):
        client, _, _ = make_client(ok({}))
        with self.assertRaises(TripoError):
            client.upload_image(b"x", "a.jpg")


class CreateTaskTests(unittest.TestCase):
    def test_image_to_model_request(self):
        client, session, _ = make_client(ok({"task_id": "task-1"}))
        self.assertEqual(client.create_image_to_model("file_abc"), "task-1")
        (method, url, kwargs), = session.calls
        self.assertEqual((method, url), ("POST", API + "/generation/image-to-model"))
        self.assertEqual(kwargs["json"], {"input": "file_abc", "model": MODEL})
        self.assertEqual(kwargs["headers"], {"Authorization": f"Bearer {KEY}"})
        self.assertEqual(kwargs["timeout"], (10.0, 30.0))
        self.assertIs(kwargs["allow_redirects"], False)

    def test_face_limit_only_when_set(self):
        client, session, _ = make_client(ok({"task_id": "a"}))
        client.create_image_to_model("tok")
        self.assertNotIn("face_limit", session.calls[0][2]["json"])

        client, session, _ = make_client(ok({"task_id": "b"}), ok({"task_id": "c"}), face_limit=50000)
        client.create_image_to_model("tok")
        client.create_multiview_to_model({"front": "a", "back": "b"})
        self.assertEqual(session.calls[0][2]["json"], {"input": "tok", "model": MODEL, "face_limit": 50000})
        self.assertEqual(session.calls[1][2]["json"]["face_limit"], 50000)

    def test_custom_model(self):
        client, session, _ = make_client(ok({"task_id": "a"}), model="v3.0-20250812")
        client.create_image_to_model("tok")
        self.assertEqual(session.calls[0][2]["json"]["model"], "v3.0-20250812")

    def test_multiview_request_uses_inputs_in_view_key_form(self):
        client, session, _ = make_client(ok({"task_id": "mv-1"}))
        views = {"right": "t4", "front": "t1", "back": "t3", "left": "t2"}  # order does not matter
        self.assertEqual(client.create_multiview_to_model(views), "mv-1")
        (method, url, kwargs), = session.calls
        self.assertEqual((method, url), ("POST", API + "/generation/multiview-to-model"))
        self.assertEqual(
            kwargs["json"],
            {"inputs": [{"front": "t1"}, {"left": "t2"}, {"back": "t3"}, {"right": "t4"}], "model": MODEL},
        )
        self.assertNotIn("input", kwargs["json"])

    def test_multiview_with_two_views(self):
        client, session, _ = make_client(ok({"task_id": "mv-2"}))
        client.create_multiview_to_model({"front": "t1", "back": "t3"})
        self.assertEqual(session.calls[0][2]["json"]["inputs"], [{"front": "t1"}, {"back": "t3"}])

    def test_multiview_blank_tokens_count_as_omitted_views(self):
        client, session, _ = make_client(ok({"task_id": "mv-3"}))
        client.create_multiview_to_model({"front": "t1", "left": "   ", "back": "t3", "right": None})
        self.assertEqual(session.calls[0][2]["json"]["inputs"], [{"front": "t1"}, {"back": "t3"}])

    def test_multiview_rejects_bad_views_without_network(self):
        client, session, _ = make_client()
        bad_inputs = {
            "no front": {"left": "a", "back": "b"},
            "one view": {"front": "a"},
            "empty": {},
            "unknown key": {"front": "a", "top": "b"},
            "blank token is ignored": {"front": "a", "left": ""},
            "whitespace token is ignored": {"front": "a", "left": "  "},
            "front missing but blank": {"front": "", "left": "a", "back": "b"},
            "token of the wrong type": {"front": "a", "left": 5},
            "not a dict": ["a", "b"],
        }
        for label, views in bad_inputs.items():
            with self.subTest(label):
                with self.assertRaises(TripoError):
                    client.create_multiview_to_model(views)
        self.assertEqual(session.calls, [])

    def test_create_rejects_empty_token(self):
        client, session, _ = make_client()
        for token in ("", "  ", None):
            with self.assertRaises(TripoInputError):
                client.create_image_to_model(token)
        self.assertEqual(session.calls, [])

    def test_error_envelope_becomes_tripo_error_with_hint(self):
        envelope = {
            "code": 2010,
            "status": "error",
            "message": "You don't have enough credit to create this task",
            "suggestion": "Please purchase more credit",
            "request_id": "req_abc123",
        }
        client, session, clock = make_client(FakeResponse(403, envelope))
        with self.assertRaises(TripoError) as ctx:
            client.create_image_to_model("tok")
        err = ctx.exception
        self.assertEqual(err.code, 2010)
        self.assertEqual(err.http_status, 403)
        self.assertEqual(err.request_id, "req_abc123")
        self.assertEqual(err.suggestion, "Please purchase more credit")
        self.assertIn("кредит", err.hint)
        self.assertIn("enough credit", str(err))
        self.assertIn("2010", str(err))
        self.assertFalse(err.transient)
        self.assertFalse(err.maybe_created)
        self.assertEqual((len(session.calls), clock.sleeps), (1, []))

    def test_success_requires_http_2xx_and_code_zero(self):
        client, _, _ = make_client(FakeResponse(200, {"code": 1004, "status": "error", "message": "bad param"}))
        with self.assertRaises(TripoError) as ctx:
            client.create_image_to_model("tok")
        self.assertEqual(ctx.exception.code, 1004)
        self.assertFalse(ctx.exception.maybe_created)  # a definite rejection

        client, _, _ = make_client(FakeResponse(400, {"code": 0, "data": {"task_id": "x"}}))
        with self.assertRaises(TripoError):
            client.create_image_to_model("tok")

    def test_answer_without_task_id_is_an_error(self):
        client, _, _ = make_client(ok({}))
        with self.assertRaises(TripoError) as ctx:
            client.create_image_to_model("tok")
        self.assertTrue(ctx.exception.maybe_created)

    # --- retries: creating a task is paid, so only HTTP 429 may be repeated

    def test_create_retried_on_429_honouring_retry_after(self):
        client, session, clock = make_client(
            api_error(429, 2000, "limit", headers={"Retry-After": "3"}),
            ok({"task_id": "t9"}),
        )
        self.assertEqual(client.create_image_to_model("tok"), "t9")
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(clock.sleeps, [3.0])

    def test_create_429_without_retry_after_uses_backoff(self):
        client, session, clock = make_client(api_error(429, 1007), api_error(429, 1007), ok({"task_id": "t9"}))
        self.assertEqual(client.create_image_to_model("tok"), "t9")
        self.assertEqual(len(session.calls), 3)
        self.assertEqual(clock.sleeps, [1.0, 2.0])

    def test_create_gives_up_after_three_429(self):
        client, session, clock = make_client(api_error(429, 1007), api_error(429, 1007), api_error(429, 1007))
        with self.assertRaises(TripoError) as ctx:
            client.create_image_to_model("tok")
        self.assertEqual(ctx.exception.code, 1007)
        self.assertEqual(ctx.exception.http_status, 429)
        self.assertTrue(ctx.exception.hint)
        self.assertFalse(ctx.exception.maybe_created)
        self.assertEqual(len(session.calls), 3)
        self.assertEqual(clock.sleeps, [1.0, 2.0])

    def test_create_429_with_zero_retry_after_stops_after_exactly_three_posts(self):
        # Retry-After: 0 would make an uncapped loop spin forever; the attempt cap must stop it.
        # It does not mean "retry at once" either: the back-off step still applies.
        client, session, clock = make_client(*[api_error(429, 1007, headers={"Retry-After": "0"}) for _ in range(10)])
        with self.assertRaises(TripoError) as ctx:
            client.create_image_to_model("tok")
        self.assertEqual(ctx.exception.http_status, 429)
        self.assertEqual(len(session.calls), 3)
        self.assertEqual(clock.sleeps, [1.0, 2.0])

    def test_the_wait_is_the_larger_of_back_off_step_and_retry_after(self):
        cases = {"0": [1.0, 2.0], "0.5": [1.0, 2.0], "1": [1.0, 2.0], "1.5": [1.5, 2.0], "3": [3.0, 3.0], "20": [20.0, 20.0]}
        for retry_after, expected in cases.items():
            with self.subTest(retry_after=retry_after):
                client, session, clock = make_client(
                    *[api_error(429, 1007, headers={"Retry-After": retry_after}) for _ in range(5)])
                with self.assertRaises(TripoError):
                    client.create_image_to_model("tok")
                self.assertEqual(clock.sleeps, expected)

    def test_create_stops_when_retry_after_exceeds_wait_budget(self):
        client, session, clock = make_client(api_error(429, 2000, "limit", headers={"Retry-After": "120"}))
        with self.assertRaises(TripoError) as ctx:
            client.create_image_to_model("tok")
        self.assertEqual(ctx.exception.code, 2000)
        self.assertIn("120", str(ctx.exception))
        self.assertTrue(ctx.exception.hint)
        self.assertEqual((len(session.calls), clock.sleeps), (1, []))

    def test_create_wait_budget_is_cumulative_and_inclusive(self):
        # 40 s + 40 s would be 80 s in total: the second wait is refused after the first was served
        client, session, clock = make_client(*[api_error(429, 2000, headers={"Retry-After": "40"}) for _ in range(5)])
        with self.assertRaises(TripoError):
            client.create_image_to_model("tok")
        self.assertEqual((len(session.calls), clock.sleeps), (2, [40.0]))
        # 30 s + 30 s is exactly the 60 s budget: allowed, and the third attempt is the last one
        client, session, clock = make_client(*[api_error(429, 2000, headers={"Retry-After": "30"}) for _ in range(5)])
        with self.assertRaises(TripoError):
            client.create_image_to_model("tok")
        self.assertEqual((len(session.calls), clock.sleeps), (3, [30.0, 30.0]))

    def test_budget_message_reports_the_cumulative_wait(self):
        client, session, clock = make_client(
            api_error(429, 1007, headers={"Retry-After": "60"}),
            api_error(429, 1007, headers={"Retry-After": "1"}),
            ok({"task_id": "t"}),
        )
        with self.assertRaises(TripoError) as ctx:
            client.create_image_to_model("tok")
        self.assertEqual((len(session.calls), clock.sleeps), (2, [60.0]))
        self.assertIn("already waited 60 s", str(ctx.exception))
        self.assertIn("60 s limit", str(ctx.exception))

    def test_fractional_response_codes_are_never_zero(self):
        client, _, _ = make_client(FakeResponse(200, {"code": 0.5, "data": {"task_id": "t"}}))
        with self.assertRaises(TripoError):
            client.create_image_to_model("tok")
        client, _, _ = make_client(FakeResponse(200, {"code": 0.0, "data": {"task_id": "t"}}),
                                   FakeResponse(200, {"code": "0", "data": {"task_id": "u"}}))
        self.assertEqual(client.create_image_to_model("tok"), "t")  # exact zeros are fine
        self.assertEqual(client.create_image_to_model("tok"), "u")

    def test_unusable_retry_after_falls_back_to_backoff(self):
        for header in ("Wed, 21 Oct 2015 07:28:00 GMT", "soon", "-5", "nan", ""):
            with self.subTest(header=header):
                client, _, clock = make_client(
                    api_error(429, 1007, headers={"Retry-After": header}), ok({"task_id": "t"}))
                client.create_image_to_model("tok")
                self.assertEqual(clock.sleeps, [1.0])

    def test_create_never_retried_after_ambiguous_failures(self):
        for label, failure in AMBIGUOUS_FAILURES.items():
            with self.subTest(label):
                client, session, clock = make_client(failure())
                with self.assertRaises(TripoError) as ctx:
                    client.create_image_to_model("tok")
                self.assertEqual(len(session.calls), 1)  # exactly one POST: a blind retry could bill twice
                self.assertEqual(clock.sleeps, [])
                self.assertTrue(ctx.exception.maybe_created)
                self.assertIn("may still have been created", str(ctx.exception))

    def test_create_never_retried_when_tripo_could_not_be_reached(self):
        for label, failure in UNREACHABLE_FAILURES.items():
            with self.subTest(label):
                client, session, clock = make_client(failure())
                with self.assertRaises(TripoError) as ctx:
                    client.create_image_to_model("tok")
                self.assertEqual(len(session.calls), 1)
                self.assertEqual(clock.sleeps, [])
                self.assertFalse(ctx.exception.maybe_created)
                self.assertTrue(ctx.exception.transient)
                text = str(ctx.exception).lower()
                self.assertIn("could not reach", text)
                self.assertNotIn("may still have been created", text)

    def test_ssl_error_on_create_is_one_post_with_a_plain_message(self):
        for failure in (requests.exceptions.SSLError("certificate verify failed"), tls_certificate_failure(),
                        tls_handshake_alert()):
            with self.subTest(str(failure)[:60]):
                client, session, clock = make_client(failure)
                with self.assertRaises(TripoError) as ctx:
                    client.create_image_to_model("tok")
                self.assertEqual((len(session.calls), clock.sleeps), (1, []))
                self.assertIn("could not reach", str(ctx.exception).lower())
                self.assertNotIn("created", str(ctx.exception).lower())
                self.assertFalse(ctx.exception.maybe_created)

    def test_tls_error_after_the_request_was_sent_may_have_created_the_task(self):
        # A TLS failure is not always a handshake failure: when the peer cuts the session after the
        # request went out, the task may exist.  Calling that "could not reach Tripo" would let the
        # caller submit a second paid task.
        for failure in (tls_eof_after_send, tls_garbage_after_send):
            with self.subTest(failure.__name__):
                for create in (lambda c: c.create_image_to_model("tok"),
                               lambda c: c.create_multiview_to_model({"front": "a", "left": "b"})):
                    client, session, clock = make_client(failure())
                    with self.assertRaises(TripoError) as ctx:
                        create(client)
                    self.assertEqual((len(session.calls), clock.sleeps), (1, []))
                    self.assertTrue(ctx.exception.maybe_created)
                    self.assertIn("may still have been created", str(ctx.exception))
                    self.assertNotIn("could not reach", str(ctx.exception).lower())

    def test_create_multiview_is_never_retried_after_500(self):
        client, session, _ = make_client(api_error(500, 1000))
        with self.assertRaises(TripoError) as ctx:
            client.create_multiview_to_model({"front": "a", "left": "b"})
        self.assertEqual(len(session.calls), 1)
        self.assertIn("may still have been created", str(ctx.exception))

    def test_create_5xx_with_huge_retry_after_raises_at_once(self):
        client, session, clock = make_client(api_error(503, 1001, headers={"Retry-After": "86400"}))
        with self.assertRaises(TripoError):
            client.create_image_to_model("tok")
        self.assertEqual((len(session.calls), clock.sleeps), (1, []))

    def test_maybe_created_only_for_unclear_outcomes(self):
        cases = [
            ([FakeResponse(400, {"code": 1004, "message": "bad"})], False),    # API refused it
            ([FakeResponse(401, {"code": 2, "message": "auth"})], False),
            ([FakeResponse(403, {"code": 2010, "message": "credits"})], False),
            ([html_error(429)] * 3, False),                                    # a 429 (retried) even without envelope
            ([FakeResponse(500, {"code": 1000, "message": "boom"})], True),    # server failed: unclear
            ([html_error(502)], True),                                         # gateway page: unclear
            ([html_error(408)], True),
            ([FakeResponse(404, not_json=True, text="Not found")], True),      # not an API answer
        ]
        for script, expected in cases:
            with self.subTest(status=script[0].status_code, text=script[0].text[:20]):
                client, _, _ = make_client(*script)
                with self.assertRaises(TripoError) as ctx:
                    client.create_image_to_model("tok")
                self.assertEqual(ctx.exception.maybe_created, expected)

    def test_invalid_base_url_is_reported_not_retried(self):
        session = FakeSession(requests.exceptions.MissingSchema("No scheme supplied"))
        client = TripoClient(KEY, base_url="tripo.example.test/v3", session=session, sleep=lambda s: None)
        with self.assertRaises(TripoError) as ctx:
            client.upload_image(b"x", "a.jpg")
        self.assertIn("TRIPO_BASE_URL", ctx.exception.hint)
        self.assertEqual(len(session.calls), 1)

    def test_there_is_no_fallback_to_another_host(self):
        client, session, clock = make_client(dns_failure(), ok({"task_id": "t1"}))
        with self.assertRaises(TripoError):
            client.create_image_to_model("tok")
        self.assertEqual(session.urls(), [API + "/generation/image-to-model"])  # one URL, one attempt

        client, session, clock = make_client(*[dns_failure() for _ in range(3)])
        with self.assertRaises(TripoError):
            client.upload_image(b"x", "a.jpg")
        self.assertEqual(set(session.urls()), {API + "/files"})  # retried, but always on the same host
        self.assertEqual(len(session.calls), 3)

    # --- some requests exceptions describe the RESPONSE: they come after the request went out

    def test_invalid_header_or_url_errors_may_have_created_the_task(self):
        failures = {
            "malformed response header":
                lambda: requests.exceptions.InvalidHeader("Content-Length contained multiple unmatching values (5, 7)"),
            "bad redirect location":
                lambda: requests.exceptions.InvalidURL("Invalid percent-escape sequence: 'zz'"),
        }
        for label, failure in failures.items():
            for create in (lambda c: c.create_image_to_model("tok"),
                           lambda c: c.create_multiview_to_model({"front": "a", "left": "b"})):
                with self.subTest(label):
                    client, session, clock = make_client(failure())
                    with self.assertRaises(TripoError) as ctx:
                        create(client)
                    self.assertEqual((len(session.calls), clock.sleeps), (1, []))
                    self.assertTrue(ctx.exception.maybe_created)
                    self.assertIn("may still have been created", str(ctx.exception))

    def test_errors_raised_while_preparing_the_request_mean_nothing_was_sent(self):
        for failure in (requests.exceptions.MissingSchema("No scheme supplied"),
                        requests.exceptions.InvalidSchema("No connection adapters were found")):
            with self.subTest(type(failure).__name__):
                client, session, clock = make_client(failure)
                with self.assertRaises(TripoError) as ctx:
                    client.create_image_to_model("tok")
                self.assertEqual((len(session.calls), clock.sleeps), (1, []))
                self.assertFalse(ctx.exception.maybe_created)
                self.assertNotIn("may still have been created", str(ctx.exception))

    # --- redirects are never followed for API calls

    def test_redirect_answers_are_errors_and_are_never_followed(self):
        calls = {
            "create": lambda c: c.create_image_to_model("tok"),
            "multiview": lambda c: c.create_multiview_to_model({"front": "a", "left": "b"}),
            "upload": lambda c: c.upload_image(b"x", "a.jpg"),
            "poll": lambda c: c.get_task("t1"),
        }
        for status in (301, 302, 303, 307, 308):
            for label, call in calls.items():
                with self.subTest(status=status, call=label):
                    redirect = FakeResponse(status, headers={"Location": "/v3/elsewhere"}, not_json=True, text="")
                    client, session, clock = make_client(redirect, ok({"task_id": "x", "file_token": "f"}))
                    with self.assertRaises(TripoError) as ctx:
                        call(client)
                    err = ctx.exception
                    self.assertEqual(err.http_status, status)
                    self.assertFalse(err.maybe_created)  # a redirect means the request was not processed
                    self.assertFalse(err.transient)
                    self.assertIn("redirect", str(err).lower())
                    self.assertEqual((len(session.calls), clock.sleeps), (1, []))  # nothing retried or followed

    def test_credentials_in_the_base_url_stay_out_of_error_text(self):
        url = "https://user:SECRETPW@api.example.test:8443/v3"
        for failure in (connect_timeout(), mid_flight_reset(), requests.exceptions.MissingSchema("x"),
                        requests.exceptions.InvalidHeader("x")):
            with self.subTest(type(failure).__name__):
                client, _, _ = make_client(failure, base_url=url)
                with self.assertRaises(TripoError) as ctx:
                    client.create_image_to_model("tok")
                text = str(ctx.exception)
                self.assertNotIn("SECRETPW", text)
                self.assertNotIn("user:", text)
                self.assertIn("api.example.test:8443", text)


class LocalErrorTests(unittest.TestCase):
    """Failures that requests does not wrap must still come out as TripoError."""

    LOCAL_ERRORS = {
        "unusable CA bundle": lambda: OSError("Could not find a suitable TLS CA certificate bundle, invalid path"),
        "unencodable header": lambda: UnicodeEncodeError("latin-1", "с", 0, 1, "ordinal not in range(256)"),
        "value error": lambda: ValueError("bad value"),
    }

    def test_upload_and_create_report_local_errors_without_retrying(self):
        for label, failure in self.LOCAL_ERRORS.items():
            with self.subTest(label):
                for call in (lambda c: c.upload_image(b"x", "a.jpg"),
                             lambda c: c.create_image_to_model("tok"),
                             lambda c: c.get_task("t1")):
                    client, session, clock = make_client(failure())
                    with self.assertRaises(TripoError) as ctx:
                        call(client)
                    self.assertFalse(ctx.exception.transient)
                    self.assertFalse(ctx.exception.maybe_created)
                    self.assertTrue(ctx.exception.hint)
                    self.assertEqual((len(session.calls), clock.sleeps), (1, []))
                    self.assertNotIn(KEY, str(ctx.exception))

    def test_download_reports_local_errors(self):
        for label, failure in self.LOCAL_ERRORS.items():
            with self.subTest(label):
                client, session, clock = make_client(failure())
                with self.assertRaises(TripoError) as ctx:
                    client.download("https://cdn.tripo3d.ai/out/model.glb")
                self.assertFalse(ctx.exception.transient)
                self.assertEqual((len(session.calls), clock.sleeps), (1, []))


class GetTaskTests(unittest.TestCase):
    def test_parses_successful_task(self):
        data = {
            "task_id": "t1",
            "type": "image_to_model",
            "status": "SUCCESS",
            "progress": 100,
            "output": {
                "model_url": "https://cdn.tripo3d.ai/out/model.glb",
                "rendered_image_url": "https://cdn.tripo3d.ai/out/preview.png",
            },
            "credits_consumed": 100.0,
            "created_at": "2026-04-28T12:00:00Z",
            "completed_at": "2026-04-28T12:01:30Z",
        }
        client, session, _ = make_client(ok(data))
        task = client.get_task("t1")
        (method, url, kwargs), = session.calls
        self.assertEqual((method, url), ("GET", API + "/tasks/t1"))
        self.assertEqual(kwargs["headers"], {"Authorization": f"Bearer {KEY}"})
        self.assertEqual(kwargs["timeout"], (10.0, 30.0))
        self.assertIs(kwargs["allow_redirects"], False)
        self.assertIsInstance(task, TripoTask)
        self.assertEqual(task.task_id, "t1")
        self.assertEqual(task.status, "success")
        self.assertTrue(task.is_success)
        self.assertFalse(task.is_failed)
        self.assertEqual(task.progress, 100)
        self.assertEqual(task.model_url, "https://cdn.tripo3d.ai/out/model.glb")
        self.assertEqual(task.rendered_image_url, "https://cdn.tripo3d.ai/out/preview.png")
        self.assertEqual(task.raw, data)

    def test_parses_failed_task(self):
        client, _, _ = make_client(task_resp("failed", 37, error_code=2008, error_message="bad image"))
        task = client.get_task("t1")
        self.assertTrue(task.is_failed)
        self.assertEqual((task.error_code, task.error_message), (2008, "bad image"))
        self.assertIsNone(task.model_url)
        self.assertEqual(task.progress, 37)

    def test_task_error_code_must_be_an_exact_integer(self):
        for value, expected in ((7, 7), (7.0, 7), ("7", 7), (7.5, None), ("x", None), (True, None), (None, None)):
            with self.subTest(value=value):
                client, _, _ = make_client(task_resp("failed", 5, error_code=value))
                self.assertEqual(client.get_task("t1").error_code, expected)

    def test_missing_or_odd_fields_do_not_break_parsing(self):
        client, _, _ = make_client(ok({"status": None, "progress": "x", "output": "nope"}))
        task = client.get_task("t1")
        self.assertEqual((task.task_id, task.status, task.progress), ("t1", "unknown", 0))
        self.assertIsNone(task.model_url)

    def test_non_string_status_is_unknown_not_an_exception(self):
        for value in (7, None, ["success"], {"status": "success"}, 3.5, True, ""):
            with self.subTest(value=value):
                client, _, _ = make_client(ok({"status": value, "progress": 10}))
                task = client.get_task("t1")
                self.assertEqual(task.status, "unknown")
                self.assertFalse(task.is_success or task.is_failed)

    def test_task_id_is_url_quoted(self):
        client, session, _ = make_client(task_resp("running"))
        client.get_task("a/b c")
        self.assertEqual(session.urls(), [API + "/tasks/a%2Fb%20c"])

    def test_task_id_must_not_be_empty(self):
        client, session, _ = make_client()
        with self.assertRaises(TripoInputError):
            client.get_task("")
        self.assertEqual(session.calls, [])

    def test_polling_uses_the_configured_base_url_only(self):
        client, session, clock = make_client(connect_timeout(), dns_failure(), task_resp("running", 5), base_url=CHINA)
        task = client.get_task("t1")
        self.assertEqual(task.status, "running")
        self.assertEqual(session.urls(), [CHINA + "/tasks/t1"] * 3)  # retried on the same URL
        self.assertEqual(clock.sleeps, [1.0, 2.0])

    def test_unknown_task_is_a_plain_error(self):
        client, session, _ = make_client(api_error(404, 2001, "The task is not found"))
        with self.assertRaises(TripoError) as ctx:
            client.get_task("nope")
        self.assertEqual(ctx.exception.code, 2001)
        self.assertEqual(len(session.calls), 1)

    def test_three_failing_attempts_sleep_exactly_one_and_two_seconds(self):
        failures = {
            "http 503": lambda: api_error(503, 1000),
            "http 500 html": lambda: html_error(500),
            "http 429": lambda: api_error(429, 1007),
            "connection error": lambda: requests.exceptions.ConnectionError("network down"),
            "read timeout": lambda: requests.exceptions.ReadTimeout("slow"),
            "ssl error": lambda: requests.exceptions.SSLError("handshake failed"),
            "broken body": lambda: requests.exceptions.ChunkedEncodingError("connection broken"),
            "dns failure": dns_failure,
        }
        for label, failure in failures.items():
            with self.subTest(label):
                client, session, clock = make_client(*[failure() for _ in range(5)])
                with self.assertRaises(TripoError) as ctx:
                    client.get_task("t1")
                self.assertTrue(ctx.exception.transient)
                self.assertEqual(len(session.calls), 3)
                self.assertEqual(clock.sleeps, [1.0, 2.0])  # nothing is slept after the last attempt

    def test_5xx_with_zero_retry_after_stops_after_exactly_three_gets(self):
        client, session, clock = make_client(*[api_error(503, 1000, headers={"Retry-After": "0"}) for _ in range(10)])
        with self.assertRaises(TripoError):
            client.get_task("t1")
        self.assertEqual(len(session.calls), 3)
        self.assertEqual(clock.sleeps, [1.0, 2.0])  # Retry-After: 0 does not remove the back-off

    def test_5xx_with_huge_retry_after_raises_without_sleeping_that_long(self):
        for status, retry_after in ((503, "86400"), (500, "3600"), (502, "61"), (429, "86400")):
            with self.subTest(status=status, retry_after=retry_after):
                client, session, clock = make_client(
                    api_error(status, 1000, headers={"Retry-After": retry_after}), finished_resp())
                with self.assertRaises(TripoError) as ctx:
                    client.get_task("t1")
                self.assertEqual(ctx.exception.http_status, status)
                self.assertIn(retry_after, str(ctx.exception))
                self.assertEqual((len(session.calls), clock.sleeps), (1, []))

    def test_retry_after_within_the_budget_is_honoured(self):
        client, session, clock = make_client(api_error(503, 1000, headers={"Retry-After": "60"}), finished_resp())
        self.assertTrue(client.get_task("t1").is_success)
        self.assertEqual(clock.sleeps, [60.0])

    NOT_AN_ENVELOPE = {
        "html page": lambda: FakeResponse(200, not_json=True, text="<html>Please log in to the proxy</html>"),
        "empty body": lambda: FakeResponse(200, not_json=True, text=""),
        "json without code": lambda: FakeResponse(200, {"data": {"task_id": "t1"}}),
        "json list": lambda: FakeResponse(200, [1, 2]),
    }

    def test_a_200_answer_that_is_not_an_envelope_is_retried(self):
        for label, bad in self.NOT_AN_ENVELOPE.items():
            with self.subTest(label):
                client, session, clock = make_client(bad(), finished_resp())
                self.assertTrue(client.get_task("t1").is_success)
                self.assertEqual((len(session.calls), clock.sleeps), (2, [1.0]))

                client, session, clock = make_client(bad(), bad(), bad(), finished_resp())
                with self.assertRaises(TripoError) as ctx:
                    client.get_task("t1")
                self.assertTrue(ctx.exception.transient)
                self.assertTrue(ctx.exception.maybe_created)
                self.assertEqual((len(session.calls), clock.sleeps), (3, [1.0, 2.0]))

                client, session, clock = make_client(bad(), ok({"file_token": "f"}))
                self.assertEqual(client.upload_image(b"x", "a.jpg"), "f")  # an upload is safe to repeat too
                self.assertEqual(len(session.calls), 2)

    def test_a_200_answer_that_is_not_an_envelope_never_repeats_a_create(self):
        for label, bad in self.NOT_AN_ENVELOPE.items():
            with self.subTest(label):
                client, session, clock = make_client(bad(), ok({"task_id": "second"}))
                with self.assertRaises(TripoError) as ctx:
                    client.create_image_to_model("tok")
                self.assertEqual((len(session.calls), clock.sleeps), (1, []))
                self.assertTrue(ctx.exception.maybe_created)
                self.assertIn("may still have been created", str(ctx.exception))


class ExtractModelUrlTests(unittest.TestCase):
    def test_prefers_model_url_then_legacy_keys(self):
        extract = TripoClient.extract_model_url
        self.assertEqual(extract({"model_url": "https://a/x.glb", "pbr_model": "https://b/y.glb"}), "https://a/x.glb")
        self.assertEqual(extract({"pbr_model": "https://b/y.glb", "model": "https://c/z.glb"}), "https://b/y.glb")
        self.assertEqual(extract({"model": "https://c/z.glb", "base_model": "https://d/w.glb"}), "https://c/z.glb")
        self.assertEqual(extract({"base_model": "http://d/w.glb"}), "http://d/w.glb")

    def test_ignores_version_strings_and_non_http_values(self):
        extract = TripoClient.extract_model_url
        self.assertIsNone(extract({"model": "v3.1-20260211"}))
        self.assertIsNone(extract({"model_url": "ftp://host/x.glb", "pbr_model": "not a url", "model": 5}))
        self.assertIsNone(extract({"model_url": {"url": "https://x"}, "pbr_model": ["https://y"]}))
        # an unusable first value does not hide a good later one
        self.assertEqual(extract({"model_url": "", "model": "v3.1-20260211", "pbr_model": "https://ok/m.glb"}),
                         "https://ok/m.glb")

    def test_only_looks_at_the_output_object(self):
        extract = TripoClient.extract_model_url
        for value in (None, {}, "https://x", ["https://x"], 5):
            self.assertIsNone(extract(value))
        self.assertIsNone(extract({"rendered_image_url": "https://x/p.png"}))
        self.assertIsNone(extract({"result": {"model_url": "https://x"}}))


class WaitForTaskTests(unittest.TestCase):
    def test_returns_when_task_succeeds_and_reports_progress(self):
        client, session, clock = make_client(task_resp("queued"), task_resp("running", 40), finished_resp())
        seen = []
        task = client.wait_for_task("t1", on_progress=lambda progress, status: seen.append((progress, status)))
        self.assertEqual(task.model_url, "https://cdn.tripo3d.ai/out/model.glb")
        self.assertEqual(seen, [(0, "queued"), (40, "running"), (100, "success")])
        self.assertEqual(len(session.calls), 3)
        self.assertEqual(clock.sleeps, [2.0, 2.0])  # default poll interval

    def test_poll_interval_is_honoured(self):
        client, _, clock = make_client(task_resp("running"), finished_resp())
        client.wait_for_task("t1", poll_interval=0.5)
        self.assertEqual(clock.sleeps, [0.5])

    def test_a_sleep_never_goes_past_the_deadline(self):
        client, session, clock = make_client(task_resp("running"), task_resp("running"))
        with self.assertRaises(TripoError) as ctx:
            client.wait_for_task("t1", poll_interval=100000, timeout=900)
        self.assertIn("Timed out", str(ctx.exception))
        self.assertEqual(clock.sleeps, [900.0])  # not 100000 s
        self.assertEqual(clock.now, 900.0)
        self.assertEqual(len(session.calls), 2)  # the poll at the deadline still happens

        client, session, clock = make_client(*[task_resp("running") for _ in range(5)])
        with self.assertRaises(TripoError):
            client.wait_for_task("t1", poll_interval=10, timeout=5)
        self.assertEqual((clock.sleeps, len(session.calls)), ([5.0], 2))

    def test_the_poll_at_the_deadline_can_still_succeed(self):
        client, _, clock = make_client(task_resp("running"), finished_resp())
        self.assertTrue(client.wait_for_task("t1", poll_interval=10, timeout=5).is_success)
        self.assertEqual(clock.sleeps, [5.0])

    def test_zero_poll_interval_is_floored_so_it_cannot_spin(self):
        client, session, clock = make_client(*[task_resp("running") for _ in range(200)])
        with self.assertRaises(TripoError):
            client.wait_for_task("t1", poll_interval=0, timeout=1.0)
        self.assertTrue(all(s >= 0.05 for s in clock.sleeps[:-1]))  # the last sleep is cut to the deadline
        self.assertLess(len(session.calls), 30)

    def test_invalid_poll_interval_or_timeout_is_rejected_without_network(self):
        client, session, _ = make_client()
        bad_arguments = (
            {"poll_interval": -1}, {"poll_interval": float("nan")}, {"poll_interval": float("inf")},
            {"poll_interval": "x"}, {"poll_interval": None},
            {"timeout": float("nan")}, {"timeout": float("inf")}, {"timeout": -5}, {"timeout": "soon"},
            {"timeout": None},
        )
        for kwargs in bad_arguments:
            with self.subTest(kwargs=str(kwargs)):
                with self.assertRaises(TripoInputError):
                    client.wait_for_task("t1", **kwargs)
        self.assertEqual(session.calls, [])

    def test_failed_task_raises_with_error_details_in_the_message_only(self):
        client, _, _ = make_client(task_resp("running", 10), task_resp(
            "failed", 10, error_code=1234, error_message="Mesh generation failed"))
        with self.assertRaises(TripoError) as ctx:
            client.wait_for_task("t1")
        err = ctx.exception
        self.assertIn("Mesh generation failed", str(err))
        self.assertIn("1234", str(err))
        self.assertIn("t1", str(err))
        self.assertEqual(err.task_error_code, 1234)
        self.assertIsNone(err.code)  # the task's code must not pose as a request-level code
        self.assertTrue(err.hint)

    def test_failed_task_without_error_code(self):
        client, _, _ = make_client(task_resp("failed", 10))
        with self.assertRaises(TripoError) as ctx:
            client.wait_for_task("t1")
        self.assertIsNone(ctx.exception.task_error_code)
        self.assertIsNone(ctx.exception.code)

    def test_every_terminal_failure_status_raises(self):
        for status in ("failed", "cancelled", "banned", "expired"):
            with self.subTest(status):
                client, session, _ = make_client(task_resp(status))
                with self.assertRaises(TripoError) as ctx:
                    client.wait_for_task("t1")
                self.assertIn(status, str(ctx.exception))
                self.assertEqual(len(session.calls), 1)

    def test_unknown_status_keeps_polling_until_timeout_and_logs_once(self):
        client, session, clock = make_client(*[task_resp("unknown") for _ in range(50)])
        with self.assertLogs(tripo_client.logger, level="WARNING") as logs:
            with self.assertRaises(TripoError) as ctx:
                client.wait_for_task("t1", poll_interval=2.0, timeout=10.0)
        self.assertIn("Timed out", str(ctx.exception))
        self.assertIn("t1", str(ctx.exception))
        self.assertIn("unknown", str(ctx.exception))
        self.assertEqual(len(session.calls), 6)  # polls at t = 0, 2, 4, 6, 8, 10
        self.assertEqual(clock.now, 10.0)
        self.assertEqual(len(logs.records), 1)  # logged once, not on every poll

    def test_unknown_status_followed_by_success_returns(self):
        client, _, _ = make_client(task_resp("paused"), task_resp("paused"), finished_resp())
        with self.assertLogs(tripo_client.logger, level="WARNING") as logs:
            task = client.wait_for_task("t1")
        self.assertTrue(task.is_success)
        self.assertEqual(len(logs.records), 1)

    def test_non_string_status_keeps_polling(self):
        client, session, _ = make_client(ok({"status": 7, "progress": 5}), ok({"status": None}), finished_resp())
        with self.assertLogs(tripo_client.logger, level="WARNING"):
            self.assertTrue(client.wait_for_task("t1").is_success)
        self.assertEqual(len(session.calls), 3)

    def test_times_out_while_running(self):
        client, session, _ = make_client(*[task_resp("running", 50) for _ in range(50)])
        with self.assertRaises(TripoError) as ctx:
            client.wait_for_task("t1", poll_interval=5.0, timeout=20.0)
        self.assertIn("Timed out", str(ctx.exception))
        self.assertIn("running", str(ctx.exception))
        self.assertEqual(len(session.calls), 5)

    def test_success_without_url_raises_after_bounded_extra_polls(self):
        client, session, clock = make_client(*[task_resp("success", 100, output={"model": "v3.1-20260211"})
                                               for _ in range(20)])
        with self.assertRaises(TripoError) as ctx:
            client.wait_for_task("t1")
        self.assertIn("succeeded but returned no model URL", str(ctx.exception))
        self.assertEqual(len(session.calls), 4)  # the success poll plus three more
        self.assertEqual(clock.sleeps, [2.0, 2.0, 2.0])

    def test_success_without_url_recovers_when_url_appears(self):
        client, session, _ = make_client(task_resp("success", 100), task_resp("success", 100), finished_resp())
        task = client.wait_for_task("t1")
        self.assertIsNotNone(task.model_url)
        self.assertEqual(len(session.calls), 3)

    def test_transient_poll_failures_are_tolerated(self):
        failing_poll = [api_error(503, 1000)] * 3  # get_task makes three attempts per poll
        client, session, clock = make_client(*failing_poll, task_resp("running", 20), finished_resp())
        task = client.wait_for_task("t1")
        self.assertTrue(task.is_success)
        self.assertEqual(len(session.calls), 5)
        self.assertEqual(clock.sleeps, [1.0, 2.0, 4.0, 2.0])  # back-off inside the poll, pause, next poll

    def test_an_outage_of_one_minute_is_survived(self):
        outcomes = {
            "connection error": lambda: requests.exceptions.ConnectionError("network down"),
            "dns failure": dns_failure,
            "read timeout": lambda: requests.exceptions.ReadTimeout("slow"),
            "ssl error": lambda: requests.exceptions.SSLError("handshake failed"),
            "http 503": lambda: api_error(503, 1000),
            "http 502 page": lambda: html_error(502),
            "http 429": lambda: api_error(429, 1007, headers={"Retry-After": "2"}),
        }
        for label, outcome in outcomes.items():
            with self.subTest(label):
                clock = FakeClock()
                client, session, _ = make_client(handler=outage_until(clock, 60.0, outcome), clock=clock)
                task = client.wait_for_task("t1")
                self.assertTrue(task.is_success)
                self.assertGreaterEqual(clock.now, 60.0)

    def test_a_proxy_page_instead_of_the_answer_does_not_abort_the_wait(self):
        page = lambda: FakeResponse(200, not_json=True, text="<html>proxy</html>")  # noqa: E731
        client, session, _ = make_client(task_resp("running", 10), page(), page(), page(), finished_resp())
        self.assertTrue(client.wait_for_task("t1").is_success)
        self.assertEqual(len(session.calls), 5)

        clock = FakeClock()  # a whole minute of such answers is an outage like any other
        client, _, _ = make_client(handler=outage_until(clock, 60.0, page), clock=clock)
        self.assertTrue(client.wait_for_task("t1").is_success)

        clock = FakeClock()  # ... and it still ends after three minutes
        client, _, _ = make_client(handler=outage_until(clock, 10 ** 9, page), clock=clock)
        with self.assertRaises(TripoError) as ctx:
            client.wait_for_task("t1")
        self.assertIn("Lost contact", str(ctx.exception))

    def test_an_outage_longer_than_three_minutes_raises(self):
        clock = FakeClock()
        outcome = lambda: requests.exceptions.ConnectionError("network down")  # noqa: E731
        client, session, _ = make_client(handler=outage_until(clock, 10 ** 9, outcome), clock=clock)
        with self.assertRaises(TripoError) as ctx:
            client.wait_for_task("t1")
        self.assertIn("Lost contact", str(ctx.exception))
        self.assertIn("t1", str(ctx.exception))
        self.assertTrue(ctx.exception.transient)
        self.assertGreaterEqual(clock.now, 180.0)  # it did hold out for the full three minutes
        self.assertLess(clock.now, 260.0)

    def test_an_outage_just_under_three_minutes_is_survived(self):
        clock = FakeClock()
        outcome = lambda: api_error(503, 1000)  # noqa: E731
        client, _, _ = make_client(handler=outage_until(clock, 150.0, outcome), clock=clock)
        self.assertTrue(client.wait_for_task("t1").is_success)

    def test_successful_polls_reset_the_outage_clock(self):
        clock = FakeClock()
        outcome = lambda: requests.exceptions.ConnectionError("network down")  # noqa: E731
        # 100 polls at 2 s each take 200 s of fake time, more than the 180 s limit...
        script = [task_resp("running", 30) for _ in range(100)]
        # ...and then a one-minute outage must still be tolerated, because every good poll restarted the clock.
        client, session, _ = make_client(*script, handler=outage_until(clock, 262.0, outcome), clock=clock)
        task = client.wait_for_task("t1")
        self.assertTrue(task.is_success)
        self.assertGreater(clock.now, 200.0)
        self.assertGreater(len(session.calls), 101)  # it really went through failing polls

    def test_the_outage_clock_restarts_after_each_recovery(self):
        clock = FakeClock()
        flaky = {"until": 100.0}

        def handler(method, url, kwargs):
            if clock.now < flaky["until"]:
                return requests.exceptions.ConnectionError("network down")
            return task_resp("running", 10) if clock.now < 400.0 else finished_resp()

        # outage 0-100 s, recovery, then a second outage from t = 250 s to 350 s: neither is > 180 s on its own
        def second_outage(method, url, kwargs):
            if 250.0 <= clock.now < 350.0:
                return requests.exceptions.ConnectionError("network down")
            return handler(method, url, kwargs)

        client, _, _ = make_client(handler=second_outage, clock=clock)
        self.assertTrue(client.wait_for_task("t1").is_success)
        self.assertGreaterEqual(clock.now, 400.0)

    def test_overall_timeout_still_applies_during_an_outage(self):
        clock = FakeClock()
        outcome = lambda: requests.exceptions.ConnectionError("network down")  # noqa: E731
        client, _, _ = make_client(handler=outage_until(clock, 10 ** 9, outcome), clock=clock)
        with self.assertRaises(TripoError) as ctx:
            client.wait_for_task("t1", timeout=50.0)
        self.assertIn("Timed out", str(ctx.exception))

    def test_permanent_errors_while_polling_raise_at_once(self):
        for status, code in ((401, 2), (403, 2010), (404, 2001), (400, 1004), (422, 1004)):
            with self.subTest(status=status):
                client, session, clock = make_client(task_resp("running"), api_error(status, code), finished_resp())
                with self.assertRaises(TripoError) as ctx:
                    client.wait_for_task("t1")
                self.assertEqual(ctx.exception.http_status, status)
                self.assertEqual(len(session.calls), 2)  # one good poll, then the permanent error
                self.assertEqual(clock.sleeps, [2.0])

    def test_broken_progress_callback_does_not_abort_the_wait(self):
        client, _, _ = make_client(task_resp("running"), finished_resp())

        def broken(progress, status):
            raise RuntimeError("cannot print")

        self.assertTrue(client.wait_for_task("t1", on_progress=broken).is_success)


class DownloadTests(unittest.TestCase):
    URL = "https://cdn.tripo3d.ai/out/model.glb?Signature=SECRETSIG&Expires=1"

    def test_downloads_glb_without_credentials(self):
        response = FakeResponse(200, body=GLB)
        client, session, _ = make_client(response)
        data = client.download(self.URL)
        self.assertEqual(data, GLB)
        self.assertIs(type(data), bytes)  # the working bytearray is not handed out
        (method, url, kwargs), = session.calls
        self.assertEqual((method, url), ("GET", self.URL))
        self.assertNotIn("allow_redirects", kwargs)  # the CDN redirect is followed (no credentials are sent)
        self.assertTrue(kwargs["stream"])
        self.assertEqual(kwargs["timeout"], (10.0, 180.0))
        # the signed URL must never receive the API key
        self.assertNotIn("Authorization", kwargs.get("headers") or {})
        self.assertNotIn("headers", kwargs)
        self.assertEqual(session.headers, {})
        self.assertTrue(response.closed)

    def test_rejects_non_glb_content(self):
        cases = (
            ("html", b"<html>Access denied</html>", "not a GLB"),
            ("json", b'{"a": 1}', "not a GLB"),
            ("short", b"glT", "not a GLB"),
            ("empty", b"", "empty"),
            # long enough and with a zero "length" field, so only the magic bytes give it away
            ("non-GLB with a consistent length field", b"PK\x03\x04\x00\x00\x00\x00" + b"\x00" * 4 + b"x" * 100, "not a GLB"),
        )
        for label, body, expected in cases:
            with self.subTest(label):
                client, session, clock = make_client(FakeResponse(200, body=body))
                with self.assertRaises(TripoError) as ctx:
                    client.download(self.URL)
                self.assertIn(expected, str(ctx.exception))
                self.assertNotIn("SECRETSIG", str(ctx.exception))
                self.assertEqual((len(session.calls), clock.sleeps), (1, []))  # not transient: no retry

    def test_rejects_truncated_glb(self):
        header_only = b"glTF" + struct.pack("<II", 2, 1000)  # declares 1000 bytes, only the 12 header bytes arrive
        cases = {
            "header declares 1000 bytes, body has 12": header_only,
            "one byte missing": make_glb(100)[:-1],
            "magic only": b"glTF",
            "header cut inside the length field": b"glTF\x02\x00\x00\x00\x10\x00",
        }
        for label, body in cases.items():
            with self.subTest(label):
                client, session, clock = make_client(FakeResponse(200, body=body))
                with self.assertRaises(TripoError) as ctx:
                    client.download(self.URL)
                self.assertIn("truncated", str(ctx.exception))
                self.assertNotIn("SECRETSIG", str(ctx.exception))
                self.assertEqual((len(session.calls), clock.sleeps), (1, []))

    def test_accepts_body_that_is_at_least_as_long_as_declared(self):
        for body in (make_glb(100), make_glb(100) + b"\x00\x00\x00\x00", b"glTF" + struct.pack("<II", 2, 0) + b"\x00" * 8):
            with self.subTest(length=len(body)):
                client, _, _ = make_client(FakeResponse(200, body=body))
                self.assertEqual(client.download(self.URL), body)

    def test_requires_http_200(self):
        client, session, _ = make_client(FakeResponse(403, body=GLB))
        with self.assertRaises(TripoError) as ctx:
            client.download(self.URL)
        self.assertEqual(ctx.exception.http_status, 403)
        self.assertEqual(len(session.calls), 1)  # not transient: no retry

    def test_only_http_200_counts_as_a_download(self):
        for status in (201, 202, 204, 206, 301, 304, 399):
            with self.subTest(status=status):
                client, session, _ = make_client(FakeResponse(status, body=GLB))
                with self.assertRaises(TripoError) as ctx:
                    client.download(self.URL)
                self.assertEqual(ctx.exception.http_status, status)
                self.assertEqual(len(session.calls), 1)

    def test_a_trickling_download_hits_the_wall_clock_cap(self):
        # download_timeout only bounds each single read; one attempt must not last for hours.
        clock = FakeClock()
        slow = SlowResponse(clock, [GLB[i:i + 1] for i in range(len(GLB))], seconds_per_chunk=100.0)
        client, session, _ = make_client(slow, clock=clock)
        self.assertEqual(client.max_download_seconds, 600.0)
        with self.assertRaises(TripoError) as ctx:
            client.download(self.URL)
        self.assertFalse(ctx.exception.transient)
        self.assertIn("took longer than 600 s", str(ctx.exception))
        self.assertNotIn("SECRETSIG", str(ctx.exception))
        self.assertEqual(len(session.calls), 1)  # not retried
        self.assertEqual(slow.chunks_served, 7)  # stopped at 700 s instead of reading the whole file
        self.assertTrue(slow.closed)

    def test_the_download_is_read_in_small_chunks_so_the_cap_gets_checked(self):
        # A read returns only after a whole chunk has arrived: with 64 KB chunks a slow trickle
        # would not reach the wall-clock check for many seconds.  8 KB keeps the check frequent.
        response = FakeResponse(200, body=GLB)
        client, _, _ = make_client(response)
        client.download(self.URL)
        self.assertEqual(response.chunk_sizes, [8192])
        clock = FakeClock()
        slow = SlowResponse(clock, [GLB], seconds_per_chunk=1.0)
        client, _, _ = make_client(slow, clock=clock)
        client.download(self.URL)
        self.assertEqual(slow.chunk_sizes, [8192])

    def test_a_download_within_the_wall_clock_cap_succeeds(self):
        clock = FakeClock()
        slow = SlowResponse(clock, [GLB[i:i + 8] for i in range(0, len(GLB), 8)], seconds_per_chunk=50.0)
        client, _, _ = make_client(slow, clock=clock)
        self.assertEqual(client.download(self.URL), GLB)  # 8 chunks, 400 s

        clock = FakeClock()
        slow = SlowResponse(clock, [GLB[i:i + 8] for i in range(0, len(GLB), 8)], seconds_per_chunk=2.0)
        client, _, _ = make_client(slow, clock=clock)
        client.max_download_seconds = 5.0  # the cap is adjustable
        with self.assertRaises(TripoError):
            client.download(self.URL)
        self.assertEqual(slow.chunks_served, 3)

    def test_a_huge_announced_size_is_refused_before_reading_anything(self):
        response = FakeResponse(200, headers={"Content-Length": str(10 ** 12)}, body=GLB)
        client, session, _ = make_client(response)
        with self.assertRaises(TripoError) as ctx:
            client.download(self.URL)
        self.assertIn("larger than", str(ctx.exception))
        self.assertFalse(response.iterated)
        self.assertTrue(response.closed)
        self.assertEqual(len(session.calls), 1)

    def test_an_unreadable_content_length_is_ignored(self):
        for value in ("abc", "", "1, 2", "-5", None):
            with self.subTest(value=value):
                headers = {} if value is None else {"Content-Length": value}
                client, _, _ = make_client(FakeResponse(200, headers=headers, body=GLB))
                self.assertEqual(client.download(self.URL), GLB)

    def test_the_signed_url_does_not_leak_through_the_exception_chain(self):
        url = "https://cdn.tripo3d.ai/out/model.glb?Signature=SECRETSIG&Expires=1"
        leaking = lambda: requests.exceptions.ConnectionError(  # noqa: E731
            MaxRetryError(None, "/out/model.glb?Signature=SECRETSIG&Expires=1",
                          NewConnectionError(None, f"Failed to connect to {url}")))
        scenarios = {
            "connection error": [leaking(), leaking(), leaking()],
            "local error": [OSError(f"cannot open {url}")],
            "stream interrupted": [FakeResponse(
                200, chunks=[GLB[:8], requests.exceptions.ChunkedEncodingError(f"broken {url}")])] * 3,
            "request-side error": [requests.exceptions.InvalidURL(f"bad {url}")],
        }
        for label, script in scenarios.items():
            with self.subTest(label):
                client, _, _ = make_client(*script)
                with self.assertRaises(TripoError) as ctx:
                    client.download(url)
                err = ctx.exception
                formatted = "".join(traceback.format_exception(type(err), err, err.__traceback__))
                self.assertNotIn("SECRETSIG", formatted)
                self.assertNotIn("SECRETSIG", str(err))
                self.assertIsNone(err.__cause__)

    def test_retries_transient_failures_then_succeeds(self):
        client, session, clock = make_client(
            FakeResponse(503),
            requests.exceptions.ReadTimeout("slow"),
            FakeResponse(200, body=GLB),
        )
        self.assertEqual(client.download(self.URL), GLB)
        self.assertEqual(len(session.calls), 3)
        self.assertEqual(clock.sleeps, [1.0, 2.0])

    def test_retry_after_is_honoured_for_downloads(self):
        client, _, clock = make_client(FakeResponse(429, headers={"Retry-After": "4"}), FakeResponse(200, body=GLB))
        client.download(self.URL)
        self.assertEqual(clock.sleeps, [4.0])

    def test_huge_retry_after_raises_instead_of_sleeping(self):
        client, session, clock = make_client(FakeResponse(503, headers={"Retry-After": "86400"}), FakeResponse(200, body=GLB))
        with self.assertRaises(TripoError):
            client.download(self.URL)
        self.assertEqual((len(session.calls), clock.sleeps), (1, []))

    def test_gives_up_after_three_attempts(self):
        client, session, clock = make_client(FakeResponse(503), FakeResponse(503), FakeResponse(503))
        with self.assertRaises(TripoError):
            client.download(self.URL)
        self.assertEqual(len(session.calls), 3)
        self.assertEqual(clock.sleeps, [1.0, 2.0])

    def test_network_errors_are_retried(self):
        failures = dict(AMBIGUOUS_FAILURES)
        failures.update(UNREACHABLE_FAILURES)
        for label, failure in failures.items():
            if label.startswith("http"):
                continue
            with self.subTest(label):
                client, session, clock = make_client(failure(), FakeResponse(200, body=GLB))
                self.assertEqual(client.download(self.URL), GLB)
                self.assertEqual((len(session.calls), clock.sleeps), (2, [1.0]))

    def test_a_failing_close_does_not_hide_the_result(self):
        class BadClose(FakeResponse):
            def close(self):
                raise OSError("close failed")

        client, session, _ = make_client(BadClose(200, body=GLB))
        self.assertEqual(client.download(self.URL), GLB)
        self.assertEqual(len(session.calls), 1)

        client, session, _ = make_client(BadClose(403, body=GLB))
        with self.assertRaises(TripoError) as ctx:  # the real error, not the OSError from close()
            client.download(self.URL)
        self.assertEqual(ctx.exception.http_status, 403)

    def test_interrupted_stream_is_retried(self):
        for error in (requests.exceptions.ChunkedEncodingError("connection broken"),
                      requests.exceptions.ConnectionError("reset"), ConnectionResetError("reset"),
                      ValueError("I/O operation on closed file")):
            with self.subTest(type(error).__name__):
                broken = FakeResponse(200, chunks=[GLB[:8], error])
                client, session, _ = make_client(broken, FakeResponse(200, body=GLB))
                self.assertEqual(client.download(self.URL), GLB)
                self.assertEqual(len(session.calls), 2)
                self.assertTrue(broken.closed)

    def test_refuses_non_http_urls(self):
        client, session, _ = make_client()
        for url in ("file:///etc/passwd", "ftp://host/model.glb", "", "model.glb", "http://[::1", None):
            with self.subTest(url=url):
                with self.assertRaises(TripoError):
                    client.download(url)
        self.assertEqual(session.calls, [])

    def test_size_limit(self):
        client, _, _ = make_client(FakeResponse(200, body=GLB))
        client.max_download_bytes = 16
        with self.assertRaises(TripoError):
            client.download(self.URL)

    def test_errors_do_not_leak_the_signed_url_or_credentials(self):
        url = "https://user:PASSWORD@cdn.tripo3d.ai/out/model.glb?Signature=SECRETSIG&Expires=1"
        client, _, _ = make_client(*[requests.exceptions.ConnectionError(f"failed for {url}")] * 3)
        with self.assertRaises(TripoError) as ctx:
            client.download(url)
        text = str(ctx.exception)
        self.assertNotIn("SECRETSIG", text)
        self.assertNotIn("PASSWORD", text)
        self.assertNotIn("user:", text)
        self.assertIn("cdn.tripo3d.ai", text)

        client, _, _ = make_client(FakeResponse(403))
        with self.assertRaises(TripoError) as ctx:
            client.download(url)
        self.assertNotIn("PASSWORD", str(ctx.exception))
        self.assertNotIn("SECRETSIG", str(ctx.exception))


class RobustnessTests(unittest.TestCase):
    """The pipeline has a single outer try block: the client may only ever raise TripoError."""

    def test_api_key_stays_out_of_session_headers_and_error_text(self):
        scenarios = [
            [connect_timeout()],
            [api_error(401, 2, "Authentication required")],
            [api_error(500, 1000)],
            [html_error(502)],
            [OSError("Could not find a suitable TLS CA certificate bundle")],
        ]
        for script in scenarios:
            client, session, _ = make_client(*script)
            with self.assertRaises(TripoError) as ctx:
                client.create_image_to_model("tok")
            self.assertNotIn(KEY, str(ctx.exception))
            self.assertNotIn(KEY, repr(ctx.exception))
            self.assertNotIn(KEY, repr(client))
            self.assertEqual(session.headers, {})

    def test_html_error_pages_become_tripo_errors(self):
        client, session, _ = make_client(html_error(502), html_error(502), html_error(502))
        with self.assertRaises(TripoError) as ctx:
            client.get_task("t1")
        self.assertEqual(ctx.exception.http_status, 502)
        self.assertIn("Bad gateway", str(ctx.exception))
        self.assertEqual(len(session.calls), 3)  # 5xx on a read-only call is retried

    def test_unusable_success_bodies_become_tripo_errors(self):
        bodies = {
            "html with 200": FakeResponse(200, not_json=True, text="<html>login</html>"),
            "empty 200": FakeResponse(200, not_json=True, text=""),
            "json list": FakeResponse(200, [1, 2, 3]),
            "no code": FakeResponse(200, {"data": {"task_id": "t"}}),
            "code as text": FakeResponse(200, {"code": "zero", "data": {"task_id": "t"}}),
        }
        for label, response in bodies.items():
            with self.subTest(label):
                client, _, _ = make_client(response)
                with self.assertRaises(TripoError) as ctx:
                    client.create_image_to_model("tok")
                self.assertTrue(ctx.exception.maybe_created)

    def test_data_may_be_missing_for_polling(self):
        client, _, _ = make_client(FakeResponse(200, {"code": 0}))
        task = client.get_task("t1")
        self.assertEqual(task.status, "unknown")
        self.assertFalse(task.is_success or task.is_failed)

    def test_weird_values_in_task_do_not_raise_other_exceptions(self):
        weird = ok({"status": 7, "progress": [1], "error_code": {"a": 1}, "error_message": 5, "output": []})
        client, _, _ = make_client(weird)
        task = client.get_task("t1")
        self.assertEqual(task.status, "unknown")
        self.assertEqual(task.progress, 0)
        self.assertIsNone(task.error_code)
        self.assertEqual(task.error_message, "5")


class LoopbackTests(unittest.TestCase):
    """What the real requests/urllib3 stack does, checked over a socket on 127.0.0.1.

    Nothing leaves the machine: the server is a stdlib thread, proxies are switched off, and the
    client's sleep is a no-op, so these tests are as fast as the fake-session ones.
    """

    TASK = {"task_id": "t1", "type": "image_to_model", "status": "success", "progress": 100,
            "output": {"model_url": "https://cdn.tripo3d.ai/out/model.glb"}}

    @classmethod
    def setUpClass(cls):
        # urllib3 leaves the connection of a response with a malformed header to the garbage
        # collector, so Python warns about an unclosed socket: library behaviour, not a leak of ours.
        cls._warning_filter = warnings.catch_warnings()
        cls._warning_filter.__enter__()
        warnings.simplefilter("ignore", ResourceWarning)

    @classmethod
    def tearDownClass(cls):
        gc.collect()
        cls._warning_filter.__exit__(None, None, None)

    def setUp(self):
        self.server = LoopbackServer()
        self.addCleanup(self.server.close)
        no_proxy = mock.patch.dict(os.environ, {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
        no_proxy.start()
        self.addCleanup(no_proxy.stop)
        self.session = requests.Session()
        self.session.trust_env = False  # never let a proxy setting intercept loopback traffic
        self.addCleanup(self.session.close)
        self.client = TripoClient(KEY, base_url=self.server.base_url, session=self.session, sleep=lambda s: None)

    @staticmethod
    def envelope(data):
        return 200, {"Content-Type": "application/json"}, json.dumps({"code": 0, "data": data}).encode()

    def test_a_malformed_response_header_on_create_is_one_post_and_may_have_created_the_task(self):
        path = "/v3/generation/multiview-to-model"
        # "Content-Length: 5, 7" makes urllib3 raise InvalidHeader AFTER the POST was received
        broken = (200, {"Content-Type": "application/json", "Content-Length": "5, 7"}, b"hello")
        self.server.script[("POST", path)] = [broken, self.envelope({"task_id": "second-task"})]
        with self.assertRaises(TripoError) as ctx:
            self.client.create_multiview_to_model({"front": "a", "left": "b"})
        self.assertEqual(self.server.paths(), [("POST", path)])  # exactly one POST
        self.assertTrue(ctx.exception.maybe_created)
        self.assertIn("may still have been created", str(ctx.exception))
        self.assertNotIn(KEY, str(ctx.exception))

    def test_a_307_on_create_is_not_followed(self):
        path = "/v3/generation/image-to-model"
        self.server.script[("POST", path)] = [(307, {"Location": "/v3/elsewhere"}, b"")]
        self.server.script[("POST", "/v3/elsewhere")] = [self.envelope({"task_id": "wrong-task"})]
        with self.assertRaises(TripoError) as ctx:
            self.client.create_image_to_model("tok")
        self.assertEqual(self.server.paths(), [("POST", path)])  # the redirect target was never contacted
        self.assertEqual(len(self.server.script[("POST", "/v3/elsewhere")]), 1)
        self.assertEqual(ctx.exception.http_status, 307)
        self.assertFalse(ctx.exception.maybe_created)

    def test_a_redirect_on_a_poll_is_not_followed_either(self):
        self.server.script[("GET", "/v3/tasks/t1")] = [(302, {"Location": "/v3/elsewhere"}, b"")]
        self.server.script[("GET", "/v3/elsewhere")] = [self.envelope(self.TASK)]
        with self.assertRaises(TripoError) as ctx:
            self.client.get_task("t1")
        self.assertEqual(self.server.paths(), [("GET", "/v3/tasks/t1")])
        self.assertEqual(ctx.exception.http_status, 302)

    def test_a_200_html_poll_answer_is_retried(self):
        proxy_page = (200, {"Content-Type": "text/html"}, b"<html>proxy login</html>")
        self.server.script[("GET", "/v3/tasks/t1")] = [proxy_page, (200, {}, b""), self.envelope(self.TASK)]
        task = self.client.get_task("t1")  # the empty 200 is the second bad answer, the third attempt works
        self.assertTrue(task.is_success)
        self.assertEqual(self.server.paths(), [("GET", "/v3/tasks/t1")] * 3)

    def test_a_200_html_answer_does_not_abort_wait_for_task(self):
        proxy_page = (200, {"Content-Type": "text/html"}, b"<html>proxy login</html>")
        running = self.envelope(dict(self.TASK, status="running", progress=40, output=None))
        self.server.script[("GET", "/v3/tasks/t1")] = [running, proxy_page, proxy_page, proxy_page, self.envelope(self.TASK)]
        self.assertTrue(self.client.wait_for_task("t1", poll_interval=0.01).is_success)
        self.assertEqual(len(self.server.seen), 5)

    def test_the_download_follows_a_cdn_redirect_without_credentials(self):
        glb = make_glb()
        self.server.script[("GET", "/out/model.glb")] = [(302, {"Location": "/cdn/model.glb"}, b"")]
        self.server.script[("GET", "/cdn/model.glb")] = [(200, {"Content-Type": "model/gltf-binary"}, glb)]
        data = self.client.download(f"http://127.0.0.1:{self.server.port}/out/model.glb")
        self.assertEqual(data, glb)
        self.assertEqual(self.server.paths(), [("GET", "/out/model.glb"), ("GET", "/cdn/model.glb")])
        for _, _, headers, _ in self.server.seen:
            self.assertNotIn("Authorization", headers)

    def test_a_trickling_download_is_cut_off_by_the_wall_clock_cap(self):
        # About 200 bytes every 0.05 s.  Every single read succeeds long before download_timeout, so only
        # the wall-clock cap can stop this; it must be checked while the data is still trickling in.
        stop = threading.Event()

        def trickle(handler):
            try:
                for _ in range(2000):  # roughly 100 s of trickling if the client kept reading
                    handler.wfile.write(b"x" * 200)
                    handler.wfile.flush()
                    if stop.wait(0.05):
                        return
            except OSError:  # the client hung up: the expected end
                return

        self.addCleanup(stop.set)  # runs before the server is closed, so its thread is free
        self.server.script[("GET", "/out/model.glb")] = [(200, {"Content-Length": str(200 * 2000)}, trickle)]
        client = TripoClient(KEY, base_url=self.server.base_url, session=self.session, sleep=lambda s: None,
                             download_timeout=300)
        client.max_download_seconds = 0.5
        started = time.monotonic()
        with self.assertRaises(TripoError) as ctx:
            client.download(f"http://127.0.0.1:{self.server.port}/out/model.glb")
        elapsed = time.monotonic() - started
        stop.set()
        self.assertIn("took longer than 0.5 s", str(ctx.exception))
        self.assertFalse(ctx.exception.transient)
        self.assertEqual(len(self.server.seen), 1)  # not retried
        self.assertLess(elapsed, 10)  # filling one 64 KB chunk at this rate would take about 16 s

    def test_api_requests_carry_the_key_and_the_body_over_a_real_socket(self):
        self.server.script[("POST", "/v3/generation/image-to-model")] = [self.envelope({"task_id": "t1"})]
        self.assertEqual(self.client.create_image_to_model("tok"), "t1")
        (method, path, headers, body), = self.server.seen
        self.assertEqual(headers.get("Authorization"), f"Bearer {KEY}")
        self.assertEqual(json.loads(body), {"input": "tok", "model": MODEL})


if __name__ == "__main__":
    unittest.main()
