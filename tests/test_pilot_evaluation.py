import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from evaluation.pilot import score_pilot_session, validate_pilot_session


def session(*, alerts=None, staged_count=20, exposure=3600):
    staged = [
        {"id": f"s{i}", "start_s": i * 10, "end_s": i * 10 + 4, "direction": "in"}
        for i in range(staged_count)
    ]
    if alerts is None:
        alerts = [
            {"id": f"a{i}", "start_s": i * 10, "end_s": i * 10 + 4, "direction": "in",
             "source_observed_at_s": i * 10, "dashboard_render_ack_received_at_s": i * 10 + 2}
            for i in range(staged_count)
        ]
    return {"schema_version": 1, "session_id": "pilot-1", "split": "held_out",
            "ordinary_exposure_s": exposure, "staged_crossings": staged, "alerts": alerts}


class PilotEvaluationTests(unittest.TestCase):
    def test_acceptance_is_18_of_20_and_latency_and_exposure_gates(self):
        s = session()
        s["alerts"] = s["alerts"][:18]
        result = score_pilot_session(s)
        self.assertTrue(result["passed"])
        self.assertEqual(result["matched_crossings"], 18)

    def test_missing_is_explicit_and_19_of_20_does_not_hide_gap(self):
        s = session()
        s["alerts"] = s["alerts"][:19]
        result = score_pilot_session(s)
        self.assertEqual(len(result["missing_crossings"]), 1)
        self.assertTrue(result["passed"])

    def test_late_crossing_fails_latency_gate(self):
        s = session()
        s["alerts"][0]["dashboard_render_ack_received_at_s"] = 11
        result = score_pilot_session(s)
        self.assertFalse(result["passed"])
        self.assertIn("s0", result["late_crossings"])

    def test_unknown_latency_never_passes(self):
        s = session()
        s["alerts"][0].pop("dashboard_render_ack_received_at_s")
        result = score_pilot_session(s)
        self.assertFalse(result["passed"])
        self.assertIn("s0", result["unknown_latency_crossings"])

    def test_failed_alert_and_gap_are_visible_not_negative(self):
        s = session()
        s["alerts"][0]["status"] = "failed"
        s["gaps"] = [{"start_s": 20, "end_s": 24, "reason": "capture_lost"}]
        result = score_pilot_session(s)
        self.assertIn("a0", result["failed_alerts"])
        self.assertEqual(result["gaps"][0]["reason"], "capture_lost")
        self.assertIn("s0", result["missing_crossings"])

    def test_duplicate_alert_is_reported_and_does_not_double_match(self):
        s = session()
        duplicate = dict(s["alerts"][0], id="duplicate")
        s["alerts"].append(duplicate)
        result = score_pilot_session(s)
        self.assertEqual(result["matched_crossings"], 20)
        self.assertEqual(result["duplicate_alerts"], ["duplicate"])
        self.assertTrue(result["passed"])

    def test_ordinary_false_alert_threshold_and_denominator(self):
        s = session()
        s["alerts"] = s["alerts"] + [
            {"id": f"ordinary{i}", "start_s": 400 + i, "end_s": 401 + i,
             "direction": "out", "source_observed_at_s": 400 + i,
             "dashboard_render_ack_received_at_s": 401 + i}
            for i in range(4)
        ]
        result = score_pilot_session(s)
        self.assertEqual(result["ordinary_false_alerts"], 4)
        self.assertFalse(result["passed"])

    def test_thirty_expected_with_only_eighteen_matches_does_not_pass(self):
        s = session(staged_count=30)
        s["alerts"] = s["alerts"][:18]
        self.assertFalse(score_pilot_session(s)["passed"])

    def test_wrong_denominator_does_not_pass(self):
        result = score_pilot_session(session(staged_count=19))
        self.assertFalse(result["passed"])
        self.assertFalse(result["gates"]["staged_count_at_least_20"])

    def test_cli_writes_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "session.json"
            output = Path(td) / "score.json"
            source.write_text(json.dumps(session()))
            subprocess.run([sys.executable, "-m", "evaluation.pilot", str(source), "--output", str(output)], check=True)
            self.assertTrue(json.loads(output.read_text())["passed"])


if __name__ == "__main__":
    unittest.main()

class PilotPointAlertTests(unittest.TestCase):
    def test_point_alert_matches_independent_expected_window(self):
        s = session(alerts=[{"id": "point", "start_s": 12, "end_s": 12, "direction": "in",
                             "source_observed_at_s": 12, "dashboard_render_ack_received_at_s": 14}],
                    staged_count=1)
        s["staged_crossings"][0].update(start_s=10, end_s=15)
        result = score_pilot_session(s)
        self.assertEqual(result["matched_crossings"], 1)

    def test_point_alert_outside_expected_window_is_missing(self):
        s = session(alerts=[{"id": "point", "start_s": 16, "end_s": 16, "direction": "in",
                             "source_observed_at_s": 16, "dashboard_render_ack_received_at_s": 18}],
                    staged_count=1)
        s["staged_crossings"][0].update(start_s=10, end_s=15)
        result = score_pilot_session(s)
        self.assertEqual(result["matched_crossings"], 0)
        self.assertEqual(result["missing_crossings"], ["s0"])

class PilotCoverageTests(unittest.TestCase):
    def test_ordinary_expected_overlap_is_rejected(self):
        s = session()
        s["ordinary_intervals"] = [{"id": "o", "start_s": 15, "end_s": 25}]
        with self.assertRaisesRegex(ValueError, "overlaps"):
            score_pilot_session(s)

    def test_ordinary_expected_endpoint_touch_is_allowed(self):
        s = session()
        s["ordinary_intervals"] = [{"id": "o", "start_s": 4, "end_s": 8}]
        result = score_pilot_session(s)
        self.assertTrue(result["passed"])
