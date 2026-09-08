#!/usr/bin/env python3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import uptime


class FormatDurationTests(unittest.TestCase):
    def test_seconds_only(self):
        self.assertEqual(uptime.format_duration(45), "45s")

    def test_zero(self):
        self.assertEqual(uptime.format_duration(0), "0s")

    def test_exact_minute(self):
        self.assertEqual(uptime.format_duration(60), "1 min")

    def test_minutes_and_seconds(self):
        self.assertEqual(uptime.format_duration(90), "1 min 30s")

    def test_hours(self):
        self.assertEqual(uptime.format_duration(3661), "1h 01min")


class FormatDayLabelTests(unittest.TestCase):
    def test_german_date_format(self):
        day = datetime(2026, 9, 6).date()
        self.assertEqual(uptime.format_day_label(day), "Sunday 06.09.2026")


class BuildOutagesTests(unittest.TestCase):
    def test_pairs_down_up(self):
        events = [
            {"event": "down", "timestamp": "2026-09-06T10:00:00"},
            {"event": "up", "timestamp": "2026-09-06T10:05:00"},
            {"event": "down", "timestamp": "2026-09-06T12:00:00"},
            {"event": "up", "timestamp": "2026-09-06T12:02:00"},
        ]
        outages = uptime.build_outages(events)
        self.assertEqual(len(outages), 2)
        self.assertEqual(outages[0]["start"], datetime(2026, 9, 6, 10, 0, 0))
        self.assertEqual(outages[0]["end"], datetime(2026, 9, 6, 10, 5, 0))
        self.assertNotIn("ongoing", outages[0])

    def test_trailing_unpaired_down_is_ongoing(self):
        events = [
            {"event": "down", "timestamp": "2026-09-06T10:00:00"},
            {"event": "up", "timestamp": "2026-09-06T10:05:00"},
            {"event": "down", "timestamp": "2026-09-06T12:00:00"},
        ]
        before = datetime.now()
        outages = uptime.build_outages(events)
        after = datetime.now()
        self.assertEqual(len(outages), 2)
        ongoing = outages[-1]
        self.assertTrue(ongoing["ongoing"])
        self.assertGreaterEqual(ongoing["end"], before)
        self.assertLessEqual(ongoing["end"], after)

    def test_no_events(self):
        self.assertEqual(uptime.build_outages([]), [])


class ComputeDashboardDataTests(unittest.TestCase):
    def test_counts_and_average_over_two_days(self):
        today = datetime.now().date()
        yesterday = today - timedelta(days=1)
        events = [
            {"event": "down", "timestamp": f"{yesterday}T10:00:00"},
            {"event": "up", "timestamp": f"{yesterday}T10:05:00"},
            {"event": "down", "timestamp": f"{today}T08:00:00"},
            {"event": "up", "timestamp": f"{today}T08:01:00"},
            {"event": "down", "timestamp": f"{today}T09:00:00"},
            {"event": "up", "timestamp": f"{today}T09:02:00"},
        ]
        data = uptime.compute_dashboard_data(events, days=2)

        self.assertEqual(data["total_outages"], 3)
        self.assertEqual(data["daily"][yesterday]["count"], 1)
        self.assertEqual(data["daily"][today]["count"], 2)
        self.assertAlmostEqual(data["avg_outages_per_day"], 1.5)
        self.assertFalse(data["currently_down"])

    def test_currently_down_when_trailing_down_in_range(self):
        today = datetime.now().date()
        events = [{"event": "down", "timestamp": f"{today}T08:00:00"}]
        data = uptime.compute_dashboard_data(events, days=1)
        self.assertTrue(data["currently_down"])

    def test_empty_events(self):
        data = uptime.compute_dashboard_data([], days=7)
        self.assertEqual(data["total_outages"], 0)
        self.assertEqual(data["total_downtime_seconds"], 0)
        self.assertEqual(data["avg_duration_seconds"], 0.0)
        self.assertFalse(data["currently_down"])


class LogCheckRotationTests(unittest.TestCase):
    def setUp(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        self.addCleanup(setattr, uptime, "CHECKS_LOG_FILE", uptime.CHECKS_LOG_FILE)
        self.addCleanup(setattr, uptime, "MAX_CHECKS_LOG_SIZE", uptime.MAX_CHECKS_LOG_SIZE)
        uptime.CHECKS_LOG_FILE = Path(tmpdir.name) / "checks.log"
        uptime.MAX_CHECKS_LOG_SIZE = 100

    def test_rotates_when_oversized(self):
        now = datetime.now()
        for _ in range(20):
            uptime.log_check(now, True, "ping:1.1.1.1")

        rotated = uptime.CHECKS_LOG_FILE.parent / f"{uptime.CHECKS_LOG_FILE.name}.1"
        self.assertTrue(rotated.exists())
        self.assertTrue(uptime.CHECKS_LOG_FILE.exists())
        self.assertLess(uptime.CHECKS_LOG_FILE.stat().st_size, uptime.MAX_CHECKS_LOG_SIZE * 5)


if __name__ == "__main__":
    unittest.main()
