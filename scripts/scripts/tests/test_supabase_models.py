"""Unit tests for supabase_models: a fake HTTP session and no network at all.

Run from scripts/scripts:

    python -m unittest discover -s tests -v
"""

import hashlib
import json
import math
import os
import re
import subprocess
import sys
import unittest
from decimal import Decimal
from unittest import mock

# Make "import supabase_models" work whatever the current directory is.
SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS_DIR)

import requests

import supabase_models
from supabase_models import StoredModel, clamp_crack_percentage, store_model

REF = "testprojectref"
BASE = f"https://{REF}.supabase.co"
KEY = "sb_publishable_TESTKEY_0123456789"
TASK = "task-123"
NOW = 1700000000
FILE_ID = "0b9d7c36-5a1e-4b0f-9d57-1a5c2c0f8e11"
GLB = b"glTF\x02\x00\x00\x00" + b"\x10\x00\x00\x00" + b"model-bytes" * 5
OBJECT_NAME_RULE = r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,198}[.]glb$"  # as agreed with the backend

NOT_JSON = object()


# --------------------------------------------------------------------------- fakes

class FakeResponse:
    def __init__(self, status_code=200, payload=NOT_JSON, text=None):
        self.status_code = status_code
        self._payload = payload
        if text is not None:
            self.text = text
        else:
            self.text = "" if payload is NOT_JSON else json.dumps(payload)

    def json(self):
        if self._payload is NOT_JSON:
            raise ValueError("Expecting value")
        return self._payload


class FakeSession:
    """Routes POSTs by URL (pick RPC, uploads per bucket, register RPC) and plays back scripted outcomes."""

    def __init__(self, pick=(), uploads=None, register=()):
        self.script = {"pick": list(pick), "register": list(register)}
        for bucket, outcomes in (uploads or {}).items():
            self.script["upload:" + bucket] = list(outcomes)
        self.calls = []  # (route, url, kwargs)
        self.closed = False

    @staticmethod
    def _route(url):
        if url.endswith("/rest/v1/rpc/pick_model_bucket"):
            return "pick"
        if url.endswith("/rest/v1/rpc/register_model_file"):
            return "register"
        marker = "/storage/v1/object/"
        if marker in url:
            return "upload:" + url.split(marker, 1)[1].split("/", 1)[0]
        raise AssertionError(f"unknown URL {url}")

    def post(self, url, **kwargs):
        route = self._route(url)
        self.calls.append((route, url, kwargs))
        outcomes = self.script.get(route)
        if not outcomes:
            raise AssertionError(f"unexpected request: {route} ({url})")
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def routes(self):
        return [route for route, _, _ in self.calls]

    def close(self):
        self.closed = True


def picked(bucket="models-01"):
    return FakeResponse(200, bucket)


def pg_error(status, code, message="error"):
    return FakeResponse(status, {"code": code, "message": message, "details": None, "hint": None})


def uploaded():
    return FakeResponse(200, {"Key": "bucket/object.glb"})


def rls_denied():
    return FakeResponse(403, {"statusCode": "403", "error": "Unauthorized",
                              "message": "new row violates row-level security policy"})


def registered():
    return FakeResponse(200, FILE_ID)


def happy_session(bucket="models-01"):
    return FakeSession(pick=[picked(bucket)], uploads={bucket: [uploaded()]}, register=[registered()])


def run(session, data=GLB, **kwargs):
    """store_model with a frozen clock and a collecting logger; returns (result, log lines)."""
    logs = []
    kwargs.setdefault("base_url", REF)
    kwargs.setdefault("api_key", KEY)
    with mock.patch.object(supabase_models, "time", mock.Mock(time=lambda: NOW + 0.9)):
        stored = store_model(data, session=session, log=logs.append, **kwargs)
    return stored, logs


def expected_name(data=GLB):
    return f"artifact_model_{NOW}_{hashlib.sha256(data).hexdigest()[:8]}.glb"


# --------------------------------------------------------------------------- tests

class HappyPathTests(unittest.TestCase):
    def test_pick_upload_register_and_public_url(self):
        session = happy_session("models-02")
        stored, logs = run(session, tripo_task_id=TASK)
        sha = hashlib.sha256(GLB).hexdigest()
        name = expected_name()
        self.assertEqual(
            stored,
            StoredModel(url=f"{BASE}/storage/v1/object/public/models-02/{name}", bucket_id="models-02",
                        object_path=name, size_bytes=len(GLB), sha256=sha, file_id=FILE_ID),
        )
        self.assertEqual(session.routes(), ["pick", "upload:models-02", "register"])
        (_, pick_url, pick_kw), (_, up_url, up_kw), (_, reg_url, reg_kw) = session.calls
        self.assertEqual(pick_url, BASE + "/rest/v1/rpc/pick_model_bucket")
        self.assertEqual(pick_kw["json"], {"p_size_bytes": len(GLB)})
        self.assertEqual(up_url, f"{BASE}/storage/v1/object/models-02/{name}")
        self.assertEqual(up_kw["data"], GLB)
        self.assertNotIn("json", up_kw)
        self.assertEqual(reg_url, BASE + "/rest/v1/rpc/register_model_file")
        self.assertEqual(reg_kw["json"], {"p_bucket_id": "models-02", "p_object_path": name, "p_sha256": sha,
                                          "p_source": "tripo", "p_tripo_task_id": TASK})
        self.assertTrue(logs)

    def test_headers_and_timeouts_of_every_request(self):
        session = happy_session()
        run(session)
        content_types = {"pick": "application/json", "upload:models-01": "model/gltf-binary",
                         "register": "application/json"}
        for route, _, kwargs in session.calls:
            self.assertEqual(kwargs["timeout"], 60)
            headers = kwargs["headers"]
            self.assertEqual(headers["apikey"], KEY)
            self.assertEqual(headers["Authorization"], f"Bearer {KEY}")
            self.assertEqual(headers["Content-Type"], content_types[route])

    def test_custom_timeout_and_source_and_no_task_id(self):
        session = happy_session()
        stored, _ = run(session, timeout=7, source="manual")
        self.assertIsNotNone(stored)
        for _, _, kwargs in session.calls:
            self.assertEqual(kwargs["timeout"], 7)
        payload = session.calls[-1][2]["json"]
        self.assertEqual(payload["p_source"], "manual")
        self.assertNotIn("p_tripo_task_id", payload)

    def test_object_name_follows_the_agreed_pattern(self):
        for data in (GLB, b"x", b"glTF" + os.urandom(2000)):
            with self.subTest(size=len(data)):
                session = happy_session()
                stored, _ = run(session, data=data)
                sha = hashlib.sha256(data).hexdigest()
                self.assertEqual(stored.object_path, f"artifact_model_{NOW}_{sha[:8]}.glb")
                self.assertTrue(re.fullmatch(OBJECT_NAME_RULE, stored.object_path))
                self.assertEqual(stored.sha256, sha)
                self.assertEqual(stored.sha256, stored.sha256.lower())
                self.assertEqual(len(stored.sha256), 64)
                self.assertEqual(stored.size_bytes, len(data))

    def test_other_bucket_names_from_the_server_are_used_as_given(self):
        for bucket in ("models-01", "models-04", "3d1"):
            session = happy_session(bucket)
            stored, _ = run(session)
            self.assertEqual(stored.bucket_id, bucket)
            self.assertIn(f"/public/{bucket}/", stored.url)

    def test_bytearray_and_memoryview_are_sent_as_plain_bytes(self):
        for data in (bytearray(GLB), memoryview(GLB)):
            with self.subTest(type(data).__name__):
                session = happy_session()
                stored, _ = run(session, data=data)
                self.assertIsNotNone(stored)
                self.assertIs(type(session.calls[1][2]["data"]), bytes)  # requests would stream a bytearray
                self.assertEqual(session.calls[1][2]["data"], GLB)

    def test_a_session_of_our_own_is_closed_and_a_given_one_is_not(self):
        own = happy_session()
        with mock.patch.object(supabase_models.requests, "Session", return_value=own):
            logs = []
            with mock.patch.object(supabase_models, "time", mock.Mock(time=lambda: NOW)):
                stored = store_model(GLB, REF, KEY, log=logs.append)
        self.assertIsNotNone(stored)
        self.assertTrue(own.closed)

        given = happy_session()
        run(given)
        self.assertFalse(given.closed)


class ServerRefusalTests(unittest.TestCase):
    def test_storage_full_ends_the_attempt_without_any_upload(self):
        for status in (400, 500, 503):
            with self.subTest(status=status):
                session = FakeSession(pick=[pg_error(status, "53100", "storage_full")],
                                      uploads={"3d1": [uploaded()]}, register=[registered()])
                stored, logs = run(session)
                self.assertIsNone(stored)
                self.assertEqual(session.routes(), ["pick"])  # the legacy bucket shares the quota: no fallback
                self.assertTrue(any("53100" in line for line in logs))

    def test_too_large_or_invalid_size_ends_the_attempt(self):
        for code in ("54000", "22023"):
            with self.subTest(code=code):
                session = FakeSession(pick=[pg_error(400, code)], uploads={"3d1": [uploaded()]})
                stored, logs = run(session)
                self.assertIsNone(stored)
                self.assertEqual(session.routes(), ["pick"])
                self.assertTrue(any(code in line for line in logs))

    def test_the_refusal_code_is_read_from_the_body_whatever_the_status(self):
        for status in (400, 413, 422, 503):
            session = FakeSession(pick=[pg_error(status, "53100")], uploads={"3d1": [uploaded()]})
            stored, _ = run(session)
            self.assertIsNone(stored)
            self.assertEqual(session.routes(), ["pick"])


class FallbackTests(unittest.TestCase):
    """The legacy bucket is only for a project without the RPC.  Anywhere else it would bypass the shared quota."""

    # The RPC does not exist (a project without the migrations): the fallback bucket is used.
    RPC_MISSING = {
        "404 with PGRST202": lambda: pg_error(404, "PGRST202", "Could not find the function"),
        "404 with 42883": lambda: pg_error(404, "42883", "function does not exist"),
        "400 with 42883": lambda: pg_error(400, "42883", "function does not exist"),
        "400 with PGRST202": lambda: pg_error(400, "PGRST202", "Could not find the function"),
        "404 json without a code": lambda: FakeResponse(404, {"message": "Not found"}),
        "404 plain text": lambda: FakeResponse(404, text="Not Found"),
        "404 html page": lambda: FakeResponse(404, text="<html>Not found</html>"),
    }

    # Every other answer or failure: nothing may be uploaded, in no bucket.
    RPC_FAILURES = {
        "500": lambda: pg_error(500, "XX000", "boom"),
        "502 html page": lambda: FakeResponse(502, text="<html>Bad gateway</html>"),
        "503 without a code": lambda: FakeResponse(503, text="Service Unavailable"),
        "504": lambda: pg_error(504, "PGRST000", "gateway timeout"),
        "401": lambda: FakeResponse(401, {"message": "Invalid API key"}),
        "401 JWT": lambda: pg_error(401, "PGRST301", "JWT expired"),
        "403": lambda: FakeResponse(403, {"message": "permission denied"}),
        "400 with another code": lambda: pg_error(400, "22P02", "invalid input syntax"),
        "409": lambda: pg_error(409, "23505", "duplicate"),
        "network error": lambda: requests.exceptions.ConnectionError("network down"),
        "connect timeout": lambda: requests.exceptions.ConnectTimeout("connect timed out"),
        "read timeout": lambda: requests.exceptions.ReadTimeout("slow"),
        "ssl error": lambda: requests.exceptions.SSLError("handshake failed"),
        "unexpected exception": lambda: RuntimeError("boom"),
        "200 with json null": lambda: FakeResponse(200, None),
        "200 with an object": lambda: FakeResponse(200, {"bucket": "models-01"}),
        "200 with a number": lambda: FakeResponse(200, 5),
        "200 with an empty string": lambda: FakeResponse(200, ""),
        "200 with an unsafe bucket id": lambda: FakeResponse(200, "../etc"),
        "200 with a path": lambda: FakeResponse(200, "models/01"),
        "200 with a space": lambda: FakeResponse(200, "models 01"),
        "200 that is not json": lambda: FakeResponse(200, text="models-01"),
        "201 instead of 200": lambda: FakeResponse(201, "models-01"),
        "204": lambda: FakeResponse(204, text=""),
    }

    def test_a_missing_rpc_selects_the_fallback_bucket(self):
        for label, problem in self.RPC_MISSING.items():
            with self.subTest(label):
                session = FakeSession(pick=[problem()], uploads={"3d1": [uploaded()]}, register=[registered()])
                stored, logs = run(session, tripo_task_id=TASK)
                self.assertIsNotNone(stored)
                self.assertEqual(stored.bucket_id, "3d1")
                self.assertIn("/public/3d1/", stored.url)
                self.assertEqual(session.routes(), ["pick", "upload:3d1", "register"])
                self.assertEqual(session.calls[-1][2]["json"]["p_bucket_id"], "3d1")
                self.assertTrue(logs)

    def test_every_other_rpc_problem_ends_the_attempt_without_any_upload(self):
        for label, problem in self.RPC_FAILURES.items():
            with self.subTest(label):
                session = FakeSession(pick=[problem()], uploads={"3d1": [uploaded()]}, register=[registered()])
                stored, logs = run(session, tripo_task_id=TASK)
                self.assertIsNone(stored)
                self.assertEqual(session.routes(), ["pick"])  # no upload (the legacy bucket included), no registration
                self.assertTrue(any("не сохранена" in line for line in logs))  # the log says the model was not stored

    def test_the_fallback_bucket_is_configurable(self):
        session = FakeSession(pick=[pg_error(404, "PGRST202")], uploads={"legacy-models": [uploaded()]},
                              register=[registered()])
        stored, _ = run(session, fallback_bucket="legacy-models")
        self.assertEqual(stored.bucket_id, "legacy-models")

    UPLOAD_REFUSALS = {
        "403 rls": rls_denied,
        "400 rls": lambda: FakeResponse(400, {"statusCode": "403", "message": "new row violates row-level security policy"}),
        "413": lambda: FakeResponse(413, {"message": "The object exceeded the maximum allowed size"}),
        "500": lambda: FakeResponse(500, {"message": "oops"}),
        "network error": lambda: requests.exceptions.ConnectionError("network down"),
        "timeout": lambda: requests.exceptions.ReadTimeout("slow"),
    }

    def test_a_refused_upload_into_the_picked_bucket_ends_the_attempt(self):
        for label, failure in self.UPLOAD_REFUSALS.items():
            with self.subTest(label):
                session = FakeSession(pick=[picked("models-03")],
                                      uploads={"models-03": [failure()], "3d1": [uploaded()]},
                                      register=[registered()])
                stored, logs = run(session)
                self.assertIsNone(stored)
                # one upload only: a refusing bucket means "full", and the legacy bucket is not tried instead
                self.assertEqual(session.routes(), ["pick", "upload:models-03"])
                self.assertTrue(logs)

    def test_a_failed_upload_into_the_fallback_bucket_is_not_repeated(self):
        for label, failure in self.UPLOAD_REFUSALS.items():
            for pick in (pg_error(404, "PGRST202"), picked("3d1")):
                with self.subTest(label + " / " + str(pick.status_code)):
                    session = FakeSession(pick=[pick], uploads={"3d1": [failure(), uploaded()]}, register=[registered()])
                    stored, _ = run(session)
                    self.assertIsNone(stored)
                    self.assertEqual(session.routes(), ["pick", "upload:3d1"])  # one upload, no second try

    def test_without_a_fallback_bucket_a_missing_rpc_means_no_upload(self):
        session = FakeSession(pick=[pg_error(404, "PGRST202")], uploads={"3d1": [uploaded()]})
        stored, logs = run(session, fallback_bucket=None)
        self.assertIsNone(stored)
        self.assertEqual(session.routes(), ["pick"])
        self.assertTrue(logs)
        session = FakeSession(pick=[picked("models-01")], uploads={"models-01": [uploaded()]}, register=[registered()])
        stored, _ = run(session, fallback_bucket=None)  # a working RPC does not need the fallback
        self.assertEqual(stored.bucket_id, "models-01")
        session = FakeSession(pick=[picked("models-01")], uploads={"models-01": [rls_denied()]})
        stored, _ = run(session, fallback_bucket=None)  # and a refusal has nowhere to go
        self.assertIsNone(stored)
        self.assertEqual(session.routes(), ["pick", "upload:models-01"])

    def test_an_invalid_fallback_bucket_name_is_never_used(self):
        for bad in ("../x", "a b", "", "x/y", 5):
            with self.subTest(bad=bad):
                session = FakeSession(pick=[pg_error(404, "PGRST202")], uploads={"3d1": [uploaded()]})
                stored, _ = run(session, fallback_bucket=bad)
                self.assertIsNone(stored)
                self.assertEqual(session.routes(), ["pick"])

    def test_a_full_or_refusing_server_never_reaches_the_legacy_bucket(self):
        # the quota is shared by every bucket: only a project without the RPC may use the legacy one
        for pick in (pg_error(503, "53100"), pg_error(400, "54000"), pg_error(400, "22023"),
                     requests.exceptions.ConnectionError("down"), pg_error(500, "XX000")):
            session = FakeSession(pick=[pick], uploads={"3d1": [uploaded()]}, register=[registered()])
            stored, _ = run(session, fallback_bucket="3d1")
            self.assertIsNone(stored)
            self.assertNotIn("upload:3d1", session.routes())


class RegistrationTests(unittest.TestCase):
    FAILURES = {
        "500": lambda: pg_error(500, "XX000", "boom"),
        "404 function missing": lambda: pg_error(404, "PGRST202", "Could not find the function"),
        "403": lambda: FakeResponse(403, {"message": "permission denied"}),
        "network error": lambda: requests.exceptions.ConnectionError("network down"),
        "timeout": lambda: requests.exceptions.ReadTimeout("slow"),
        "unexpected exception": lambda: RuntimeError("boom"),
    }

    def test_a_failed_registration_does_not_fail_the_call(self):
        for label, failure in self.FAILURES.items():
            with self.subTest(label):
                session = FakeSession(pick=[picked()], uploads={"models-01": [uploaded()]}, register=[failure()])
                stored, logs = run(session)
                self.assertIsNotNone(stored)
                self.assertIsNone(stored.file_id)
                self.assertEqual(stored.bucket_id, "models-01")
                self.assertEqual(session.routes(), ["pick", "upload:models-01", "register"])  # not retried
                self.assertTrue(any("Предупреждение" in line for line in logs))

    def test_registration_answers_without_an_id_leave_file_id_empty(self):
        for answer in (FakeResponse(200, text="not json"), FakeResponse(200, 5), FakeResponse(200, ""),
                       FakeResponse(200, None)):
            session = FakeSession(pick=[picked()], uploads={"models-01": [uploaded()]}, register=[answer])
            stored, _ = run(session)
            self.assertIsNotNone(stored)
            self.assertIsNone(stored.file_id)

    def test_201_counts_as_a_success_for_upload_and_registration(self):
        session = FakeSession(pick=[picked()], uploads={"models-01": [FakeResponse(201, {"Key": "k"})]},
                              register=[FakeResponse(201, FILE_ID)])
        stored, _ = run(session)
        self.assertEqual(stored.file_id, FILE_ID)

    def test_the_fallback_upload_is_registered_too(self):
        session = FakeSession(pick=[pg_error(404, "PGRST202")], uploads={"3d1": [uploaded()]}, register=[registered()])
        stored, _ = run(session, tripo_task_id=TASK)
        self.assertEqual(stored.file_id, FILE_ID)
        self.assertEqual(session.calls[-1][2]["json"]["p_bucket_id"], "3d1")


class InputTests(unittest.TestCase):
    def test_empty_input_is_refused_without_any_request(self):
        session = FakeSession()
        stored, logs = run(session, data=b"")
        self.assertIsNone(stored)
        self.assertEqual(session.calls, [])
        self.assertTrue(logs)

    def test_oversized_input_is_refused_without_any_request(self):
        self.assertEqual(supabase_models.MAX_MODEL_BYTES, 209715200)
        session = FakeSession()
        stored, logs = run(session, data=b"\0" * (209715200 + 1))
        self.assertIsNone(stored)
        self.assertEqual(session.calls, [])
        self.assertTrue(any("209715200" in line for line in logs))

    def test_the_size_limit_is_inclusive(self):
        with mock.patch.object(supabase_models, "MAX_MODEL_BYTES", 10):
            session = happy_session()
            stored, _ = run(session, data=b"0123456789")
            self.assertIsNotNone(stored)
            session = FakeSession()
            stored, _ = run(session, data=b"0123456789A")
            self.assertIsNone(stored)
            self.assertEqual(session.calls, [])

    def test_input_that_is_not_bytes_is_refused(self):
        for data in ("text", None, 5, [1, 2], {"a": 1}):
            with self.subTest(type(data).__name__):
                session = FakeSession()
                stored, _ = run(session, data=data)
                self.assertIsNone(stored)
                self.assertEqual(session.calls, [])

    def test_unusable_project_ref_or_key_is_refused_without_any_request(self):
        bad_refs = (None, "", "x.evil.example/", "a b", "ref/../", "ref#", "ref@evil.example", "ref\n", 5)
        for ref in bad_refs:
            with self.subTest(base_url=ref):
                session = FakeSession()
                stored, _ = run(session, base_url=ref)
                self.assertIsNone(stored)
                self.assertEqual(session.calls, [])
        for key in (None, "", "key with space", "ключ", "key\n", 5):
            with self.subTest(api_key=key):
                session = FakeSession()
                stored, logs = run(session, api_key=key)
                self.assertIsNone(stored)
                self.assertEqual(session.calls, [])


class SecretsTests(unittest.TestCase):
    def test_the_api_key_is_only_sent_in_headers(self):
        session = happy_session()
        run(session, tripo_task_id=TASK)
        for _, url, kwargs in session.calls:
            self.assertNotIn(KEY, url)
            self.assertNotIn(KEY, json.dumps(kwargs.get("json")))
            if "data" in kwargs:
                self.assertNotIn(KEY.encode(), kwargs["data"])

    def test_the_api_key_never_appears_in_logs_or_results(self):
        leaky_exception = lambda: requests.exceptions.InvalidHeader(  # noqa: E731
            f"Invalid return character or leading space in header value: 'Bearer {KEY}'")
        leaky_response = lambda: FakeResponse(403, {"message": f"permission denied for key {KEY}"})  # noqa: E731
        leaky_text = lambda: FakeResponse(500, text=f"<html>error for {KEY}</html>")  # noqa: E731
        missing_rpc = lambda: pg_error(404, "PGRST202", f"no function, key {KEY}")  # noqa: E731
        scenarios = {
            "pick raises": FakeSession(pick=[leaky_exception()]),
            "pick answers with the key": FakeSession(pick=[leaky_response()]),
            "pick page with the key": FakeSession(pick=[leaky_text()]),
            "missing rpc, fallback upload raises": FakeSession(pick=[missing_rpc()],
                                                               uploads={"3d1": [leaky_exception()]}),
            "missing rpc, fallback upload answers with the key": FakeSession(pick=[missing_rpc()],
                                                                             uploads={"3d1": [leaky_response()]}),
            "upload raises": FakeSession(pick=[picked()], uploads={"models-01": [leaky_exception()]}),
            "upload answers with the key": FakeSession(pick=[picked()], uploads={"models-01": [leaky_response()]}),
            "upload page with the key": FakeSession(pick=[picked()], uploads={"models-01": [leaky_text()]}),
            "register raises": FakeSession(pick=[picked()], uploads={"models-01": [uploaded()]},
                                           register=[leaky_exception()]),
            "register answers with the key": FakeSession(pick=[picked()], uploads={"models-01": [uploaded()]},
                                                         register=[leaky_response()]),
            "happy path": happy_session(),
            "storage full": FakeSession(pick=[pg_error(503, "53100", f"storage_full {KEY}")]),
        }
        for label, session in scenarios.items():
            with self.subTest(label):
                stored, logs = run(session, tripo_task_id=TASK)
                text = "\n".join(logs)
                self.assertTrue(logs)
                self.assertNotIn(KEY, text)
                self.assertNotIn(KEY, repr(stored))

    def test_the_key_is_replaced_by_a_marker_in_logged_error_texts(self):
        session = FakeSession(pick=[requests.exceptions.InvalidHeader(f"bad value Bearer {KEY}")])
        _, logs = run(session)
        joined = "\n".join(logs)
        self.assertNotIn(KEY, joined)
        self.assertIn("***", joined)  # the key was cut out, the rest of the message is still useful
        self.assertIn("InvalidHeader", joined)

    def test_a_broken_logger_does_not_lose_the_upload(self):
        def broken(message):
            raise UnicodeEncodeError("ascii", message, 0, 1, "cannot print")

        session = happy_session()
        stored = store_model(GLB, REF, KEY, session=session, log=broken)
        self.assertIsNotNone(stored)


class ClampCrackPercentageTests(unittest.TestCase):
    TABLE = [
        # missing / unusable -> 0.0
        (None, 0.0), ("", 0.0), ("   ", 0.0), ("abc", 0.0), ("12%", 0.0), ("7,5", 0.0),
        (float("nan"), 0.0), ("nan", 0.0), (float("inf"), 0.0), (float("-inf"), 0.0), ("inf", 0.0),
        ("1e999", 0.0), (10 ** 400, 0.0), (True, 0.0), (False, 0.0), ([], 0.0), ({}, 0.0), ([5], 0.0),
        (object(), 0.0),
        # below the range
        (-5, 0.0), (-0.0, 0.0), (-0.001, 0.0), ("-5", 0.0), (-1e9, 0.0),
        # inside the range
        (0, 0.0), (0.004, 0.0), (7, 7.0), (7.5, 7.5), ("7.5", 7.5), (" 7.5 ", 7.5), (Decimal("12.5"), 12.5),
        (12.345, 12.35), (12.344, 12.34), (50, 50.0), (99.99, 99.99),
        # above the range
        (100, 100.0), (100.004, 100.0), (99.996, 100.0), (150, 100.0), ("150", 100.0), (1e9, 100.0), ("1e2", 100.0),
    ]

    def test_table(self):
        for value, expected in self.TABLE:
            with self.subTest(value=repr(value)):
                result = clamp_crack_percentage(value)
                self.assertIs(type(result), float)
                self.assertEqual(result, expected)

    def test_zero_is_never_negative_zero(self):
        for value in (-0.0, -0.001, -5, None, "abc"):
            self.assertEqual(math.copysign(1.0, clamp_crack_percentage(value)), 1.0)

    def test_the_result_always_lies_in_the_database_range(self):
        for value in (-1e300, -1, 0, 0.5, 33.333333, 99.999, 100, 100.001, 1e300, float("nan"), float("inf")):
            result = clamp_crack_percentage(value)
            self.assertTrue(0.0 <= result <= 100.0)
            self.assertEqual(result, round(result, 2))


class ModuleTests(unittest.TestCase):
    def test_import_pulls_in_no_heavy_packages(self):
        code = (
            "import sys; sys.path.insert(0, sys.argv[1]); import supabase_models; "
            "heavy = {'torch', 'cv2', 'dotenv', 'numpy', 'PIL', 'scipy', 'torchvision', 'fastapi', 'pydantic', 'google'}; "
            "print(sorted(heavy & {m.split('.')[0] for m in sys.modules}))"
        )
        result = subprocess.run([sys.executable, "-I", "-c", code, SCRIPTS_DIR], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "[]")


PI = "http://192.168.1.61:54321"


class ServerUrlTests(unittest.TestCase):
    """The scripts talk to the Supabase stack on the Raspberry Pi; a cloud ref still works."""

    def test_valid_server_urls_are_normalized(self):
        cases = {
            PI: PI,
            PI + "/": PI,
            "HTTP://Pi-Server.local:54321//": "http://Pi-Server.local:54321",
            "https://abcd.supabase.co": "https://abcd.supabase.co",
            "http://[::1]:54321": "http://[::1]:54321",
            "https://example.org/supabase/": "https://example.org/supabase",
            "http://localhost": "http://localhost",
            REF: BASE,  # a bare cloud project ref
        }
        for given, expected in cases.items():
            with self.subTest(given=given):
                self.assertEqual(supabase_models.normalize_base_url(given), expected)

    def test_unusable_server_urls_are_refused(self):
        bad = (None, 5, b"http://x", "", " ", "ftp://pi:54321", "http://", "http://:54321",
               "http://user:pw@pi:54321", "http://pi@evil.example", "http://pi:54321?x=1",
               "http://pi:54321#frag", "http://pi:54321/#", "http://pi:99999", "http://pi:0",
               "http://pi:abc", "http://pi:", "http:// pi", PI + "\n", " " + PI, "http://p\u012b",
               "http://-pi-:54321", "javascript:alert(1)", "ref/../", "a b")
        for value in bad:
            with self.subTest(value=value):
                self.assertIsNone(supabase_models.normalize_base_url(value))

    def test_resolve_prefers_supabase_url_and_falls_back_to_the_project_ref(self):
        resolve = supabase_models.resolve_supabase_url
        self.assertEqual(resolve({"SUPABASE_URL": "  " + PI + "/  "}), PI)
        self.assertEqual(resolve({"SUPABASE_URL": PI, "SUPABASE_PROJECT_REF": REF}), PI)
        self.assertEqual(resolve({"SUPABASE_URL": "", "SUPABASE_PROJECT_REF": REF}), BASE)
        self.assertEqual(resolve({"SUPABASE_PROJECT_REF": " " + REF + " "}), BASE)
        self.assertIsNone(resolve({}))
        # A broken explicit setting is reported, never silently replaced by the cloud project
        self.assertIsNone(resolve({"SUPABASE_URL": "not a url", "SUPABASE_PROJECT_REF": REF}))
        self.assertIsNone(resolve({"SUPABASE_PROJECT_REF": PI}))

    def test_resolve_reads_the_process_environment_by_default(self):
        with mock.patch.dict(os.environ, {"SUPABASE_URL": PI}, clear=False):
            self.assertEqual(supabase_models.resolve_supabase_url(), PI)

    def test_every_request_and_the_public_url_use_the_pi_server(self):
        session = happy_session()
        stored, _ = run(session, base_url=PI + "/")
        self.assertIsNotNone(stored)
        urls = [url for _, url, _ in session.calls]
        self.assertEqual(urls[0], PI + "/rest/v1/rpc/pick_model_bucket")
        self.assertEqual(urls[1], f"{PI}/storage/v1/object/models-01/{stored.object_path}")
        self.assertEqual(urls[2], PI + "/rest/v1/rpc/register_model_file")
        self.assertEqual(stored.url, f"{PI}/storage/v1/object/public/models-01/{stored.object_path}")
        self.assertTrue(all(url.startswith(PI + "/") for url in urls))

    def test_unusable_server_url_sends_nothing_and_says_which_setting(self):
        session = FakeSession()
        stored, logs = run(session, base_url="http://pi:54321?debug=1")
        self.assertIsNone(stored)
        self.assertEqual(session.calls, [])
        self.assertTrue(any("SUPABASE_URL" in line for line in logs), logs)


if __name__ == "__main__":
    unittest.main()
