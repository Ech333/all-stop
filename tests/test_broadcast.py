"""Tests for the webhook broadcast module. Real local HTTP server, not mocked."""

import http.server
import json
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from all_stop.broadcast import BroadcastEvent, _message_for, _payload_for, send_broadcast  # noqa: E402


class _CapturingHandler(http.server.BaseHTTPRequestHandler):
    received_body = None
    received_content_type = None
    respond_with = 200

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        _CapturingHandler.received_body = json.loads(body.decode("utf-8"))
        _CapturingHandler.received_content_type = self.headers.get("Content-Type")
        self.send_response(_CapturingHandler.respond_with)
        self.end_headers()

    def log_message(self, *args):
        pass


class _LocalServer:
    def __enter__(self):
        _CapturingHandler.received_body = None
        _CapturingHandler.respond_with = 200
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
        return f"http://127.0.0.1:{self.port}/hook"


class MessageFormattingTests(unittest.TestCase):
    def test_tripped_message_includes_actor_and_reason(self):
        event = BroadcastEvent(kind="tripped", reason="bad server", actor="erik", at="2026-01-01T00:00:00Z")
        msg = _message_for(event)
        self.assertIn("erik", msg)
        self.assertIn("bad server", msg)
        self.assertIn("TRIPPED", msg)

    def test_reset_message_includes_actor(self):
        event = BroadcastEvent(kind="reset", reason=None, actor="maria", at="2026-01-01T00:00:00Z")
        msg = _message_for(event)
        self.assertIn("maria", msg)
        self.assertIn("reset", msg.lower())

    def test_paused_message_includes_actor_and_reason_and_is_distinguishable_from_tripped(self):
        event = BroadcastEvent(kind="paused", reason="reviewing a suspicious tool call", actor="erik", at="2026-01-01T00:00:00Z")
        msg = _message_for(event)
        self.assertIn("erik", msg)
        self.assertIn("reviewing a suspicious tool call", msg)
        self.assertIn("PAUSED", msg)
        self.assertNotIn("TRIPPED", msg)


class PayloadShapeTests(unittest.TestCase):
    def test_slack_url_gets_text_field(self):
        event = BroadcastEvent(kind="tripped", reason="x", actor="erik", at="t")
        payload = json.loads(_payload_for("https://hooks.slack.com/services/T00/B00/xyz", event))
        self.assertIn("text", payload)

    def test_teams_url_gets_text_field(self):
        event = BroadcastEvent(kind="tripped", reason="x", actor="erik", at="t")
        payload = json.loads(_payload_for("https://outlook.webhook.office.com/webhookb2/abc", event))
        self.assertIn("text", payload)

    def test_unrecognized_url_gets_structured_json(self):
        event = BroadcastEvent(kind="tripped", reason="x", actor="erik", at="t")
        payload = json.loads(_payload_for("https://internal.example.com/hooks/allstop", event))
        self.assertEqual(payload["event"], "kill_switch_tripped")
        self.assertEqual(payload["actor"], "erik")
        self.assertNotIn("text", payload)

    def test_paused_event_gets_its_own_distinct_event_name(self):
        event = BroadcastEvent(kind="paused", reason="x", actor="erik", at="t")
        payload = json.loads(_payload_for("https://internal.example.com/hooks/allstop", event))
        self.assertEqual(payload["event"], "kill_switch_paused")


class SendBroadcastTests(unittest.TestCase):
    def test_a_successful_post_returns_true(self):
        with _LocalServer() as srv:
            event = BroadcastEvent(kind="tripped", reason="x", actor="erik", at="t")
            self.assertTrue(send_broadcast(srv.url, event))

    def test_the_server_actually_receives_the_real_posted_body(self):
        with _LocalServer() as srv:
            event = BroadcastEvent(kind="tripped", reason="bad server", actor="erik", at="t")
            send_broadcast(srv.url, event)
            self.assertEqual(_CapturingHandler.received_body["actor"], "erik")

    def test_content_type_header_is_json(self):
        with _LocalServer() as srv:
            event = BroadcastEvent(kind="tripped", reason="x", actor="erik", at="t")
            send_broadcast(srv.url, event)
            self.assertEqual(_CapturingHandler.received_content_type, "application/json")

    def test_a_non_2xx_response_returns_false(self):
        with _LocalServer() as srv:
            _CapturingHandler.respond_with = 500
            event = BroadcastEvent(kind="tripped", reason="x", actor="erik", at="t")
            self.assertFalse(send_broadcast(srv.url, event))

    def test_an_unreachable_url_returns_false_not_raises(self):
        event = BroadcastEvent(kind="tripped", reason="x", actor="erik", at="t")
        self.assertFalse(send_broadcast("http://127.0.0.1:1/dead", event))

    def test_a_malformed_url_returns_false_not_raises(self):
        event = BroadcastEvent(kind="tripped", reason="x", actor="erik", at="t")
        self.assertFalse(send_broadcast("not-a-url", event))


if __name__ == "__main__":
    unittest.main()
