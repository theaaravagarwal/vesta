import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evaluation import probe_views


class FakeAnalyzer:
    model_name = "test-model"

    def __init__(self):
        self.calls = []

    def metadata(self, path):
        return 120.0

    def track_video(self, path, out, duration):
        self.track_dir = out
        return [
            {"time_s": 1.0, "track_id": "1", "normalized_box": [0.2, 0.2, 0.4, 0.5]},
            {"time_s": 50.0, "track_id": "2", "normalized_box": [0.1, 0.1, 0.2, 0.2]},
        ]

    def frames(self, path, out, start, end):
        out.mkdir(parents=True)
        frame = out / "000001.jpg"
        frame.write_bytes(b"frame")
        return [frame]

    def infer(self, frames, start, end, scene, tracks):
        self.calls.append((frames, start, end, scene, tracks))
        return [{
            "start_s": 1,
            "end_s": 2,
            "action": "climbing",
            "description": "climbing",
            "evidence": ["visible climbing"],
            "uncertainty": "",
        }]


class ProbeViewsTests(unittest.TestCase):
    def test_validation_rejects_unbounded_inputs(self):
        with self.assertRaisesRegex(ValueError, "at most 8"):
            probe_views.validate_probe(0, 8.1, [0, 0, 1, 1])
        with self.assertRaisesRegex(ValueError, "focus box"):
            probe_views.validate_probe(0, 1, [0, 0, 1.1, 1])

    def test_source_range_is_checked_before_tracking(self):
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / "v.mp4"
            video.write_bytes(b"video")
            fake = FakeAnalyzer()
            fake.metadata = lambda path: 3.0
            with patch.object(probe_views, "TemporalAnalyzer", return_value=fake):
                with self.assertRaisesRegex(ValueError, "source duration"):
                    probe_views.run_probe(video, 0, 4, [0.2, 0.2, 0.4, 0.5], Path(temp) / "o.json")
            self.assertFalse(hasattr(fake, "track_dir"))

    def test_empty_frames_and_first_call_error_write_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video, empty_output = root / "v.mp4", root / "empty.json"
            video.write_bytes(b"video")
            fake = FakeAnalyzer()
            fake.frames = lambda *args: []
            with patch.object(probe_views, "TemporalAnalyzer", return_value=fake):
                with self.assertRaisesRegex(RuntimeError, "no frames"):
                    probe_views.run_probe(video, 0, 3, [0.2, 0.2, 0.4, 0.5], empty_output)
            self.assertEqual(json.loads(empty_output.read_text())["error"], "frame extraction produced no frames")

            error_output = root / "error.json"
            fake = FakeAnalyzer()
            fake.infer = unittest.mock.Mock(side_effect=RuntimeError("first boom"))
            with patch.object(probe_views, "TemporalAnalyzer", return_value=fake):
                with self.assertRaisesRegex(RuntimeError, "first boom"):
                    probe_views.run_probe(video, 0, 3, [0.2, 0.2, 0.4, 0.5], error_output)
            self.assertEqual(json.loads(error_output.read_text())["variants"]["full"]["status"], "error")

    def test_invalid_spans_are_rejected_before_lexical_gate(self):
        candidate = {
            "start_s": float("nan"), "end_s": 2, "action": "climbing",
            "description": "climbing", "evidence": ["visible"], "uncertainty": "",
        }
        row = probe_views._gated([candidate])[0]
        self.assertEqual(row["decision"], "rejected")
        self.assertEqual(row["reason"], "invalid_span")
        candidate["start_s"], candidate["end_s"] = 0, 5
        row = probe_views._gated([candidate], 1, 4)[0]
        self.assertIsNone(row["reason"])

    def test_probe_matches_worker_clamping_and_candidate_validation(self):
        candidate = {
            "start_s": 0, "end_s": 5, "action": "climbing",
            "description": "climbing", "evidence": ["visible"], "uncertainty": "",
        }
        self.assertIsNone(probe_views._invalid_candidate_reason(candidate, 1, 4))
        candidate["start_s"], candidate["end_s"] = 0, 1
        self.assertEqual(probe_views._invalid_candidate_reason(candidate, 2, 4), "invalid_span")
        candidate["start_s"], candidate["end_s"] = 1, 2
        candidate["evidence"] = [""]
        self.assertEqual(probe_views._invalid_candidate_reason(candidate), "invalid_output")
        candidate["evidence"] = ["visible"]
        candidate["description"] = ""
        self.assertEqual(probe_views._invalid_candidate_reason(candidate), "invalid_output")

    def test_paired_probe_reuses_tracks_and_times_and_cleans_temp_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "video.mp4"
            output = root / "result.json"
            video.write_bytes(b"video")
            fake = FakeAnalyzer()
            with patch.object(probe_views, "TemporalAnalyzer", return_value=fake), patch.object(
                probe_views, "context_detail_frames", side_effect=lambda frames, observations: frames
            ):
                result = probe_views.run_probe(video, 0, 4, [0.2, 0.2, 0.4, 0.5], output)
            self.assertEqual(len(fake.calls), 2)
            self.assertEqual(fake.calls[0][1:4], fake.calls[1][1:4])
            self.assertEqual(fake.calls[0][4], fake.calls[1][4])
            self.assertEqual(result["variants"]["full"]["frame_count"], 1)
            self.assertIsNotNone(result["focus_bounds"])
            self.assertEqual(json.loads(output.read_text())["model"], "test-model")
            self.assertFalse(fake.track_dir.exists())

    def test_second_call_failure_writes_partial_result(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video, output = root / "v.mp4", root / "o.json"
            video.write_bytes(b"video")
            fake = FakeAnalyzer()
            fake.infer = unittest.mock.Mock(side_effect=[[], RuntimeError("boom")])
            with patch.object(probe_views, "TemporalAnalyzer", return_value=fake), patch.object(
                probe_views, "context_detail_frames", side_effect=lambda frames, observations: frames
            ):
                with self.assertRaisesRegex(RuntimeError, "boom"):
                    probe_views.run_probe(video, 0, 4, [0.2, 0.2, 0.4, 0.5], output)
            partial = json.loads(output.read_text())
            self.assertIn("full", partial["variants"])
            self.assertEqual(partial["variants"]["focus"]["status"], "error")

    def test_no_effective_bounds_reports_focus_no_op(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video, output = root / "v.mp4", root / "o.json"
            video.write_bytes(b"video")
            fake = FakeAnalyzer()
            with patch.object(probe_views, "TemporalAnalyzer", return_value=fake), patch.object(
                probe_views, "focus_bounds", return_value=None
            ):
                result = probe_views.run_probe(video, 0, 4, [0.2, 0.2, 0.4, 0.5], output)
            self.assertEqual(result["variants"]["focus"]["status"], "no_op")
            self.assertEqual(len(fake.calls), 1)


if __name__ == "__main__":
    unittest.main()
