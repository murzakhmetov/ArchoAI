"""Unit tests for backend_select: which server the scanner scripts use. No network at all.

The same rules are tested for the app in archoai_app/test/backend_config_test.dart.

Run from scripts/scripts:

    python -m unittest discover -s tests -v
"""

import os
import sys
import unittest
from unittest import mock

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS_DIR)

import requests

import backend_select
from backend_select import Server, ServerConfigError, load_server_config, select_server, server_from_env

PI = "http://192.168.1.61:54321"
CLOUD = "https://abcdefghijklmnop.supabase.co"


def both_servers(**extra):
    env = {
        "SUPABASE_URL": PI,
        "SUPABASE_KEY": "pi-key",
        "SUPABASE_FALLBACK_URL": CLOUD,
        "SUPABASE_FALLBACK_KEY": "cloud-key",
    }
    env.update(extra)
    return env


class FakeProbe:
    def __init__(self, up):
        self.up = set(up)
        self.asked = []

    def __call__(self, server):
        self.asked.append(server.url)
        return server.url in self.up


class LoadServerConfigTests(unittest.TestCase):
    def test_a_single_server_keeps_working_exactly_as_before(self):
        config = load_server_config({"SUPABASE_URL": PI, "SUPABASE_KEY": "pi-key"})
        self.assertEqual(config.primary, Server("primary", PI, "pi-key"))
        self.assertIsNone(config.fallback)
        self.assertEqual(config.choice, "auto")
        self.assertEqual(config.warnings, ())

    def test_project_ref_builds_the_cloud_address(self):
        config = load_server_config({"SUPABASE_PROJECT_REF": "abcdefghijklmnop", "SUPABASE_KEY": "k"})
        self.assertEqual(config.primary.url, CLOUD)
        self.assertTrue(config.primary.is_cloud)

    def test_missing_first_server_is_a_readable_error(self):
        with self.assertRaisesRegex(ServerConfigError, "SUPABASE_URL"):
            load_server_config({"SUPABASE_KEY": "k"})
        with self.assertRaisesRegex(ServerConfigError, "SUPABASE_KEY"):
            load_server_config({"SUPABASE_URL": PI})

    def test_two_servers(self):
        config = load_server_config(both_servers())
        self.assertFalse(config.primary.is_cloud)
        self.assertEqual(config.fallback, Server("fallback", CLOUD, "cloud-key"))
        self.assertTrue(config.fallback.is_cloud)

    def test_half_configured_second_server_is_ignored_with_a_warning(self):
        no_key = load_server_config(both_servers(SUPABASE_FALLBACK_KEY=""))
        self.assertIsNone(no_key.fallback)
        self.assertIn("SUPABASE_FALLBACK_KEY", no_key.warnings[0])
        no_url = load_server_config(both_servers(SUPABASE_FALLBACK_URL="  "))
        self.assertIsNone(no_url.fallback)
        self.assertIn("SUPABASE_FALLBACK_URL", no_url.warnings[0])
        bad_url = load_server_config(both_servers(SUPABASE_FALLBACK_URL="ftp://cloud"))
        self.assertIsNone(bad_url.fallback)
        self.assertTrue(bad_url.warnings)

    def test_the_same_server_twice_is_one_server(self):
        config = load_server_config(both_servers(SUPABASE_FALLBACK_URL=PI + "/"))
        self.assertIsNone(config.fallback)
        self.assertTrue(config.warnings)

    def test_values_are_trimmed_and_trailing_slashes_dropped(self):
        config = load_server_config({"SUPABASE_URL": "  " + PI + "//  ", "SUPABASE_KEY": " k "})
        self.assertEqual(config.primary.url, PI)
        self.assertEqual(config.primary.key, "k")

    def test_server_choice_is_parsed_and_unknown_values_mean_auto(self):
        self.assertEqual(load_server_config(both_servers(SUPABASE_SERVER="fallback")).choice, "fallback")
        self.assertEqual(load_server_config(both_servers(SUPABASE_SERVER=" PRIMARY ")).choice, "primary")
        unknown = load_server_config(both_servers(SUPABASE_SERVER="pi"))
        self.assertEqual(unknown.choice, "auto")
        self.assertIn("SUPABASE_SERVER", unknown.warnings[0])

    def test_with_two_servers_the_key_names_follow_the_app(self):
        # Same .env as backend_config_test.dart: the app reads SUPABASE_PUBLISHABLE_KEY first.
        config = load_server_config(both_servers(SUPABASE_PUBLISHABLE_KEY="stale"))
        self.assertEqual(config.primary.key, "stale")
        self.assertIn("different keys", config.warnings[0])
        same = load_server_config(both_servers(SUPABASE_PUBLISHABLE_KEY="pi-key"))
        self.assertEqual(same.warnings, ())

    def test_with_one_server_supabase_key_still_comes_first(self):
        config = load_server_config({"SUPABASE_URL": PI, "SUPABASE_KEY": "pi-key", "SUPABASE_ANON_KEY": "old"})
        self.assertEqual(config.primary.key, "pi-key")
        self.assertIn("different keys", config.warnings[0])

    def test_addresses_are_normalized_like_the_app_does(self):
        bare_ref = load_server_config(both_servers(SUPABASE_FALLBACK_URL="abcdefghijklmnop"))
        self.assertEqual(bare_ref.fallback.url, CLOUD)
        same_server = load_server_config(both_servers(SUPABASE_FALLBACK_URL="HTTP://192.168.1.61:54321"))
        self.assertIsNone(same_server.fallback)

    def test_reads_the_process_environment_by_default(self):
        with mock.patch.dict(os.environ, both_servers(), clear=True):
            self.assertEqual(load_server_config().fallback.url, CLOUD)


class SelectServerTests(unittest.TestCase):
    def setUp(self):
        self.config = load_server_config(both_servers())

    def test_auto_uses_the_first_server_when_it_answers(self):
        self.assertEqual(select_server(self.config, FakeProbe({PI, CLOUD})).url, PI)

    def test_auto_falls_back_to_the_second_server(self):
        self.assertEqual(select_server(self.config, FakeProbe({CLOUD})).url, CLOUD)

    def test_auto_keeps_the_first_server_when_both_are_down(self):
        self.assertEqual(select_server(self.config, FakeProbe(set())).url, PI)

    def test_a_probe_that_raises_counts_as_down(self):
        def probe(server):
            if server.url == PI:
                raise RuntimeError("network")
            return True

        self.assertEqual(select_server(self.config, probe).url, CLOUD)

    def test_a_forced_choice_needs_no_network(self):
        probe = FakeProbe(set())
        forced = load_server_config(both_servers(SUPABASE_SERVER="fallback"))
        self.assertEqual(select_server(forced, probe).url, CLOUD)
        primary = load_server_config(both_servers(SUPABASE_SERVER="primary"))
        self.assertEqual(select_server(primary, probe).url, PI)
        self.assertEqual(probe.asked, [])

    def test_a_single_server_needs_no_network(self):
        probe = FakeProbe(set())
        single = load_server_config({"SUPABASE_URL": PI, "SUPABASE_KEY": "k", "SUPABASE_SERVER": "fallback"})
        self.assertEqual(select_server(single, probe).url, PI)
        self.assertEqual(probe.asked, [])


class ProbeAndEntryPointTests(unittest.TestCase):
    def test_probe_needs_an_authenticated_200(self):
        server = Server("primary", PI, "pi-key")
        ok = mock.Mock(status_code=200)
        with mock.patch.object(backend_select.requests, "get", return_value=ok) as get:
            self.assertTrue(backend_select.probe_server(server))
        url = get.call_args.args[0]
        self.assertEqual(url, PI + "/rest/v1/sensor_data?select=id&limit=1")
        self.assertEqual(get.call_args.kwargs["headers"], {"apikey": "pi-key", "Authorization": "Bearer pi-key"})
        self.assertGreater(get.call_args.kwargs["timeout"], 0)
        with mock.patch.object(backend_select.requests, "get", return_value=mock.Mock(status_code=401)):
            self.assertFalse(backend_select.probe_server(server))
        with mock.patch.object(backend_select.requests, "get", side_effect=requests.ConnectionError("down")):
            self.assertFalse(backend_select.probe_server(server))

    def test_server_from_env_returns_none_and_says_why(self):
        logs = []
        self.assertIsNone(server_from_env({}, probe=FakeProbe(set()), log=logs.append))
        self.assertIn("SUPABASE_URL", logs[0])

    def test_server_from_env_names_the_server_in_use(self):
        logs = []
        server = server_from_env(both_servers(), probe=FakeProbe({CLOUD}), log=logs.append)
        self.assertEqual(server.url, CLOUD)
        self.assertTrue(any("Supabase Cloud" in line for line in logs))


if __name__ == "__main__":
    unittest.main()
