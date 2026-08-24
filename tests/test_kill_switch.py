"""Tests for KillSwitch. Pure stdlib unittest."""

import http.server
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from all_stop.kill_switch import KillSwitch  # noqa: E402


class _CapturingHandler(http.server.BaseHTTPRequestHandler):
    received = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        _CapturingHandler.received.append(json.loads(body.decode("utf-8")))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass  # keep test output quiet


class _LocalWebhookServer:
    """A real local HTTP server for webhook tests - not a mock. Proves send_broadcast actually
    performs a real HTTP POST, the same discipline used elsewhere in this portfolio for proving
    subprocess launches and hash-chain verification for real rather than asserting on mocks."""

    def __enter__(self):
        _CapturingHandler.received = []
        self.server = http.server.HTTPServer(("127.0.0.1", 0), _CapturingHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/webhook"


class InitialStateTests(unittest.TestCase):
    def test_a_fresh_path_is_not_tripped(self):
        with tempfile.TemporaryDirectory() as d:
            switch = KillSwitch(os.path.join(d, "state.json"))
            self.assertFalse(switch.tripped())

    def test_a_fresh_path_status_has_no_reason_or_actor(self):
        with tempfile.TemporaryDirectory() as d:
            switch = KillSwitch(os.path.join(d, "state.json"))
            status = switch.status()
            self.assertIsNone(status.reason)
            self.assertIsNone(status.actor)


class TripAndResetTests(unittest.TestCase):
    def test_trip_sets_tripped_true(self):
        with tempfile.TemporaryDirectory() as d:
            switch = KillSwitch(os.path.join(d, "state.json"))
            switch.trip("compromised server", "erik")
            self.assertTrue(switch.tripped())

    def test_trip_records_reason_and_actor(self):
        with tempfile.TemporaryDirectory() as d:
            switch = KillSwitch(os.path.join(d, "state.json"))
            status = switch.trip("compromised server", "erik")
            self.assertEqual(status.reason, "compromised server")
            self.assertEqual(status.actor, "erik")
            self.assertIsNotNone(status.tripped_at)

    def test_reset_clears_tripped(self):
        with tempfile.TemporaryDirectory() as d:
            switch = KillSwitch(os.path.join(d, "state.json"))
            switch.trip("compromised server", "erik")
            switch.reset("erik")
            self.assertFalse(switch.tripped())

    def test_reset_records_the_resetting_actor(self):
        with tempfile.TemporaryDirectory() as d:
            switch = KillSwitch(os.path.join(d, "state.json"))
            switch.trip("compromised server", "erik")
            status = switch.reset("maria")
            self.assertEqual(status.actor, "maria")
            self.assertIsNotNone(status.reset_at)

    def test_empty_reason_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            switch = KillSwitch(os.path.join(d, "state.json"))
            with self.assertRaises(ValueError):
                switch.trip("   ", "erik")

    def test_empty_actor_is_rejected_on_trip(self):
        with tempfile.TemporaryDirectory() as d:
            switch = KillSwitch(os.path.join(d, "state.json"))
            with self.assertRaises(ValueError):
                switch.trip("reason", "")

    def test_empty_actor_is_rejected_on_reset(self):
        with tempfile.TemporaryDirectory() as d:
            switch = KillSwitch(os.path.join(d, "state.json"))
            switch.trip("reason", "erik")
            with self.assertRaises(ValueError):
                switch.reset("  ")

    def test_a_rejected_trip_does_not_change_state(self):
        with tempfile.TemporaryDirectory() as d:
            switch = KillSwitch(os.path.join(d, "state.json"))
            with self.assertRaises(ValueError):
                switch.trip("reason", "")
            self.assertFalse(switch.tripped())


class MultiProcessVisibilityTests(unittest.TestCase):
    """The core promise: a second, independent KillSwitch instance pointed at the same path sees
    a trip made by the first, with no shared in-memory state and no polling loop - just a file
    read. This is what "multi-process safe by construction" actually has to prove."""

    def test_a_second_instance_sees_a_trip_made_by_the_first(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            writer = KillSwitch(path)
            reader = KillSwitch(path)
            self.assertFalse(reader.tripped())
            writer.trip("incident", "erik")
            self.assertTrue(reader.tripped())

    def test_a_second_instance_sees_a_reset_made_by_the_first(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            writer = KillSwitch(path)
            reader = KillSwitch(path)
            writer.trip("incident", "erik")
            self.assertTrue(reader.tripped())
            writer.reset("erik")
            self.assertFalse(reader.tripped())

    def test_state_survives_being_read_by_a_brand_new_instance(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            KillSwitch(path).trip("incident", "erik")
            fresh = KillSwitch(path)
            status = fresh.status()
            self.assertTrue(status.tripped)
            self.assertEqual(status.reason, "incident")


class CorruptStateFailsClosedTests(unittest.TestCase):
    """A kill switch's failure direction must be the opposite of everything else in this
    portfolio: unreadable state means "assume tripped," not "assume clear.\""""

    def test_unreadable_json_is_treated_as_tripped(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            Path(path).write_text("{not valid json", encoding="utf-8")
            switch = KillSwitch(path)
            self.assertTrue(switch.tripped())

    def test_corrupt_state_gives_a_clear_reason(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            Path(path).write_text("garbage", encoding="utf-8")
            status = KillSwitch(path).status()
            self.assertIn("unreadable", status.reason)


class WebhookBroadcastTests(unittest.TestCase):
    def test_trip_fires_a_real_http_post_to_the_configured_webhook(self):
        with tempfile.TemporaryDirectory() as d:
            with _LocalWebhookServer() as srv:
                switch = KillSwitch(os.path.join(d, "state.json"), webhook_urls=(srv.url,))
                switch.trip("incident", "erik")
                self.assertEqual(len(_CapturingHandler.received), 1)

    def test_reset_also_fires_a_broadcast(self):
        with tempfile.TemporaryDirectory() as d:
            with _LocalWebhookServer() as srv:
                switch = KillSwitch(os.path.join(d, "state.json"), webhook_urls=(srv.url,))
                switch.trip("incident", "erik")
                switch.reset("erik")
                self.assertEqual(len(_CapturingHandler.received), 2)

    def test_no_webhook_configured_means_no_request_and_no_error(self):
        with tempfile.TemporaryDirectory() as d:
            switch = KillSwitch(os.path.join(d, "state.json"))
            status = switch.trip("incident", "erik")  # must not raise
            self.assertTrue(status.tripped)

    def test_a_dead_webhook_does_not_prevent_the_trip_from_succeeding(self):
        with tempfile.TemporaryDirectory() as d:
            # Port 1 is not a webhook server and will refuse the connection.
            switch = KillSwitch(os.path.join(d, "state.json"), webhook_urls=("http://127.0.0.1:1/dead",))
            status = switch.trip("incident", "erik")
            self.assertTrue(status.tripped)

    def test_a_per_call_webhook_override_is_used_instead_of_the_constructor_default(self):
        with tempfile.TemporaryDirectory() as d:
            with _LocalWebhookServer() as srv:
                switch = KillSwitch(os.path.join(d, "state.json"))  # no webhook at construction
                switch.trip("incident", "erik", webhook_urls=(srv.url,))
                self.assertEqual(len(_CapturingHandler.received), 1)


if __name__ == "__main__":
    unittest.main()
