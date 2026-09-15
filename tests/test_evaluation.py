import unittest
from evaluation.metrics import match_events, score, validate_manifest


class MetricsTests(unittest.TestCase):
    def manifest(self, status="reviewed"):
        return {
            "schema_version": 1,
            "clips": [
                {
                    "id": "a",
                    "source_url": "https://example.test/a",
                    "license": "CC-BY-4.0",
                    "session_group": "day1",
                    "split": "held_out",
                    "path": "a.mp4",
                    "duration_s": 60,
                    "label_status": status,
                    "events": [{"action": "climbing", "start_s": 10, "end_s": 20}],
                }
            ],
        }

    def test_duplicate_is_false_alert(self):
        events = [{"action": "climbing", "start_s": 10, "end_s": 20}] * 2
        result = score(self.manifest(), {"a": {"status": "done", "events": events}})
        self.assertEqual((result["true_positive"], result["false_positive"]), (1, 1))

    def test_unknown_is_not_negative(self):
        result = score(
            self.manifest("unreviewed"), {"a": {"status": "done", "events": []}}
        )
        self.assertIsNone(result["recall"])
        self.assertFalse(result["complete"])

    def test_failed_is_not_safe(self):
        result = score(self.manifest(), {"a": {"status": "error", "events": []}})
        self.assertEqual(result["failed_predictions"], ["a"])
        self.assertIsNone(result["recall"])

    def test_unreviewed_errors_remain_visible(self):
        result = score(self.manifest("unreviewed"), {"a": {"status": "error", "events": []}})
        self.assertEqual(result["failed_predictions"], ["a"])
        self.assertEqual(result["unreviewed_clips"], ["a"])

    def test_split_leak_rejected(self):
        m = self.manifest()
        c = dict(m["clips"][0], id="b", split="development")
        m["clips"].append(c)
        with self.assertRaises(ValueError):
            validate_manifest(m)

    def test_action_mismatch(self):
        a = [{"action": "walking", "start_s": 10, "end_s": 20}]
        self.assertEqual(match_events(self.manifest()["clips"][0]["events"], a), [])

    def test_perfect_match(self):
        m = self.manifest()
        r = score(m, {"a": {"status": "done", "events": m["clips"][0]["events"]}})
        self.assertEqual(r["recall"], 1)
        self.assertEqual(r["precision"], 1)
        self.assertEqual(r["mean_start_error_s"], 0)


if __name__ == "__main__":
    unittest.main()
