"""Tests for the all-stop CLI. Calls main() directly in-process."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from all_stop.cli import main  # noqa: E402
from all_stop.kill_switch import KillSwitch  # noqa: E402


class TripCommandTests(unittest.TestCase):
    def test_trip_exits_zero_on_success(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            code = main(["trip", "--evidence", path, "--reason", "incident", "--actor", "erik"])
            self.assertEqual(code, 0)

    def test_trip_actually_trips_the_switch(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            main(["trip", "--evidence", path, "--reason", "incident", "--actor", "erik"])
            self.assertTrue(KillSwitch(path).tripped())

    def test_trip_with_empty_reason_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            code = main(["trip", "--evidence", path, "--reason", "  ", "--actor", "erik"])
            self.assertEqual(code, 1)


class StatusCommandTests(unittest.TestCase):
    def test_status_on_a_clear_switch_exits_zero(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            code = main(["status", "--evidence", path])
            self.assertEqual(code, 0)

    def test_status_on_a_tripped_switch_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            KillSwitch(path).trip("incident", "erik")
            code = main(["status", "--evidence", path])
            self.assertEqual(code, 1)


class ResetCommandTests(unittest.TestCase):
    def test_reset_clears_a_tripped_switch(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            KillSwitch(path).trip("incident", "erik")
            code = main(["reset", "--evidence", path, "--actor", "maria"])
            self.assertEqual(code, 0)
            self.assertFalse(KillSwitch(path).tripped())

    def test_reset_with_empty_actor_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            code = main(["reset", "--evidence", path, "--actor", " "])
            self.assertEqual(code, 1)


class EndToEndTests(unittest.TestCase):
    def test_full_cycle_trip_then_status_then_reset_then_status(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            self.assertEqual(main(["status", "--evidence", path]), 0)
            self.assertEqual(main(["trip", "--evidence", path, "--reason", "r", "--actor", "a"]), 0)
            self.assertEqual(main(["status", "--evidence", path]), 1)
            self.assertEqual(main(["reset", "--evidence", path, "--actor", "a"]), 0)
            self.assertEqual(main(["status", "--evidence", path]), 0)


if __name__ == "__main__":
    unittest.main()
