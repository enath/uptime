#!/usr/bin/env python3
import json
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


class ComputeCoverageGapsTests(unittest.TestCase):
    def test_gap_detected_between_distant_events(self):
        events = [
            {"event": "alive", "timestamp": "2026-09-06T08:00:00"},
            {"event": "alive", "timestamp": "2026-09-06T15:00:00"},
        ]
        gaps = uptime.compute_coverage_gaps(events, gap_threshold_seconds=1800)
        # one gap between the two events, plus a trailing gap to "now"
        self.assertEqual(gaps[0]["start"], datetime(2026, 9, 6, 8, 0, 0))
        self.assertEqual(gaps[0]["end"], datetime(2026, 9, 6, 15, 0, 0))

    def test_no_gap_when_events_are_close(self):
        events = [
            {"event": "alive", "timestamp": "2026-09-06T08:00:00"},
            {"event": "alive", "timestamp": "2026-09-06T08:05:00"},
        ]
        now = datetime(2026, 9, 6, 8, 6, 0)
        gaps = [
            g
            for g in uptime.compute_coverage_gaps(events, gap_threshold_seconds=1800)
            if g["end"] <= now
        ]
        self.assertEqual(gaps, [])

    def test_trailing_gap_to_now(self):
        old_timestamp = (datetime.now() - timedelta(hours=2)).isoformat()
        events = [{"event": "alive", "timestamp": old_timestamp}]
        before = datetime.now()
        gaps = uptime.compute_coverage_gaps(events, gap_threshold_seconds=1800)
        after = datetime.now()
        self.assertEqual(len(gaps), 1)
        self.assertGreaterEqual(gaps[0]["end"], before)
        self.assertLessEqual(gaps[0]["end"], after)

    def test_no_events_means_no_gaps(self):
        self.assertEqual(uptime.compute_coverage_gaps([]), [])


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

    def test_fully_covered_day_has_no_gap(self):
        today = datetime.now().date()
        start = datetime.combine(today, datetime.min.time())
        # a heartbeat every 20 minutes (below the 30-minute gap threshold) all day
        events = [
            {"event": "alive", "timestamp": (start + timedelta(minutes=20 * i)).isoformat()}
            for i in range(72)
        ]
        data = uptime.compute_dashboard_data(events, days=1)
        self.assertFalse(data["daily"][today]["has_gap"])
        self.assertEqual(data["monitored_days"], 1)

    def test_day_with_silence_is_flagged_as_gap(self):
        today = datetime.now().date()
        events = [
            {"event": "alive", "timestamp": f"{today}T00:05:00"},
            # nothing else all day -> a large gap covers the rest of it
        ]
        data = uptime.compute_dashboard_data(events, days=1)
        self.assertTrue(data["daily"][today]["has_gap"])
        self.assertGreater(data["daily"][today]["gap_seconds"], 0)
        self.assertEqual(data["monitored_days"], 0)


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


class AppendJsonlTests(unittest.TestCase):
    def test_heals_missing_trailing_newline(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "events.jsonl"
            # simulate a prior write cut short before its trailing newline
            path.write_text('{"event": "up", "timestamp": "2026-09-08T13:17:04"}')

            uptime.append_jsonl(path, {"event": "alive", "timestamp": "2026-09-12T21:18:03"})

            lines = path.read_text().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[0])["event"], "up")
            self.assertEqual(json.loads(lines[1])["event"], "alive")

    def test_appends_normally_when_newline_already_present(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "events.jsonl"
            path.write_text('{"event": "up", "timestamp": "2026-09-08T13:17:04"}\n')

            uptime.append_jsonl(path, {"event": "alive", "timestamp": "2026-09-12T21:18:03"})

            lines = path.read_text().splitlines()
            self.assertEqual(len(lines), 2)

    def test_creates_new_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sub" / "events.jsonl"
            uptime.append_jsonl(path, {"event": "alive", "timestamp": "2026-09-12T21:18:03"})
            self.assertEqual(len(path.read_text().splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
