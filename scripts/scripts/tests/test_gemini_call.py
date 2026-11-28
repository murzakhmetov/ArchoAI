"""Unit tests for gemini_call.py: a busy Gemini (503) must not lose a scan. No network.

Run from scripts/scripts:  python -m unittest discover -s tests -v
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gemini_call  # noqa: E402


class FakeApiError(Exception):
    """Shaped like google.genai.errors.APIError: carries the HTTP code in .code."""

    def __init__(self, code, message="error"):
        super().__init__(f"{code} {message}")
        self.code = code


class FakeModels:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)  # per call: an exception to raise, or the text to answer
        self.calls = []

    def generate_content(self, model, contents, config=None):
        self.calls.append(model)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return mock.Mock(text=outcome)


class FakeClient:
    def __init__(self, outcomes):
        self.models = FakeModels(outcomes)


def run(outcomes, primary="main-model", fallback="fb-1,fb-2"):
    client = FakeClient(outcomes)
    sleeps, logs = [], []
    with mock.patch.dict(os.environ, {"GEMINI_FALLBACK_MODELS": fallback}):
        try:
            result = gemini_call.generate(client, primary, ["photo", "prompt"], sleep=sleeps.append, log=logs.append)
        except Exception as exc:  # returned so the tests can check it
            result = exc
    return result, client.models.calls, sleeps, logs


class GenerateTests(unittest.TestCase):
    def test_answer_on_the_first_try(self):
        result, calls, sleeps, _ = run(["{}"])
        self.assertEqual(result.text, "{}")
        self.assertEqual((calls, sleeps), (["main-model"], []))

    def test_busy_model_is_retried_with_growing_pauses(self):
        result, calls, sleeps, logs = run([FakeApiError(503), FakeApiError(503), '{"name": "x"}'])
        self.assertEqual(result.text, '{"name": "x"}')
        self.assertEqual(calls, ["main-model"] * 3)
        self.assertEqual(sleeps, [3, 6])
        self.assertTrue(any("занят" in line for line in logs))

    def test_a_model_that_stays_busy_is_replaced_by_the_next_one(self):
        result, calls, _, _ = run([FakeApiError(503)] * 3 + [FakeApiError(429), "{}"])
        self.assertEqual(result.text, "{}")
        self.assertEqual(calls, ["main-model"] * 3 + ["fb-1", "fb-1"])

    def test_unavailable_model_skips_to_the_next_without_waiting(self):
        result, calls, sleeps, _ = run([FakeApiError(404), "{}"])
        self.assertEqual(result.text, "{}")
        self.assertEqual((calls, sleeps), (["main-model", "fb-1"], []))

    def test_every_model_busy_raises_the_last_error(self):
        result, calls, _, _ = run([FakeApiError(503)] * 9)
        self.assertIsInstance(result, FakeApiError)
        self.assertEqual(len(calls), 9)
        self.assertEqual(calls[-1], "fb-2")

    def test_other_errors_are_not_retried(self):
        result, calls, sleeps, _ = run([FakeApiError(400, "bad request")])
        self.assertIsInstance(result, FakeApiError)
        self.assertEqual((calls, sleeps), (["main-model"], []))

    def test_network_errors_are_retried(self):
        result, calls, _, _ = run([ConnectionError("reset"), "{}"])
        self.assertEqual(result.text, "{}")
        self.assertEqual(calls, ["main-model", "main-model"])

    def test_model_list_has_no_duplicates(self):
        with mock.patch.dict(os.environ, {"GEMINI_FALLBACK_MODELS": "a, b ,a,,main"}):
            self.assertEqual(gemini_call.model_list("main"), ["main", "a", "b"])


class ParseTests(unittest.TestCase):
    def test_plain_fenced_and_wrapped_json(self):
        for text in ('{"a": 1}', '```json\n{"a": 1}\n```', 'Here it is: {"a": 1} done'):
            with self.subTest(text=text):
                self.assertEqual(gemini_call.parse_json_object(text), {"a": 1})

    def test_no_json_raises(self):
        with self.assertRaises(ValueError):
            gemini_call.parse_json_object("sorry, I cannot help")


if __name__ == "__main__":
    unittest.main()
