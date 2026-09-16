import shutil
import tempfile
import unittest
import os
import json
from pathlib import Path
from unittest.mock import patch

import behavior
from behavior import (
    BehaviorWorker,
    Store,
    TemporalAnalyzer,
    _merge,
    _starts,
    _valid_scene,
    config_version,
    create_app,
)


class FakeAnalyzer:
    model_name = "fake-vlm"

    def metadata(self, path):
        return 12.0

    def frames(self, *args):
        return []

    def track_video(self, *args):
        return []

    def infer(self, *args):
        return []

    def suggest_scene(self, frame):
        return []


class BehaviorTests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.store = Store(self.temp)
        self.app = create_app(
            {
                "BEHAVIOR_STORE": self.store,
                "BEHAVIOR_ANALYZER": FakeAnalyzer(),
                "BEHAVIOR_START_WORKER": False,
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        shutil.rmtree(self.temp, ignore_errors=True)

    def video(self, vid="v", **overrides):
        frame = self.temp / f"{vid}.jpg"
        frame.write_bytes(b"frame")
        values = (
            vid,
            "a.mp4",
            "2026-01-01T00:00:00Z",
            None,
            None,
            12.0,
            "done",
            None,
            str(self.temp / "a.mp4"),
            str(frame),
            "empty",
            0,
            "[]",
            None,
            None,
            0,
        )
        self.store.run(
            "INSERT INTO videos VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values
        )
        return vid

    def test_temporal_windows_are_chronological_and_not_detector_gated(self):
        self.assertEqual(_starts(8), [0.0])
        self.assertEqual(_starts(12), [0.0, 4.0])
        self.assertEqual(_starts(20), [0.0, 4.0, 8.0, 12.0])

    def test_length_truncation_retries_once_and_succeeds(self):
        valid = {"events": [{"start_s": 1, "end_s": 2, "action": "climbing", "description": "person climbs visible fence", "evidence": ["frames at 1.0 and 2.0 show ascent"], "uncertainty": "partial view"}]}
        payloads = [
            {"model": "test", "choices": [{"finish_reason": "length", "message": {"content": "{\"events\":["}}]},
            {"model": "test", "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(valid)}}]},
        ]
        class Response:
            def __init__(self, data): self.data = data
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return json.dumps(self.data).encode()
        analyzer = TemporalAnalyzer()
        with patch("urllib.request.urlopen", side_effect=[Response(x) for x in payloads]) as open_mock:
            self.assertEqual(analyzer.infer([], 0, 8, {})[0]["action"], "climbing")
        self.assertEqual(open_mock.call_count, 2)

    def test_exhausted_length_is_explicit_failure(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"model":"test","choices":[{"finish_reason":"length","message":{"content":"{\\"events\\":["}}]}'
        with patch("urllib.request.urlopen", return_value=Response()):
            with self.assertRaisesRegex(RuntimeError, "attempt=2.*finish_reason=length"):
                TemporalAnalyzer().infer([], 0, 8, {})

    def test_malformed_nontruncated_response_is_explicit_failure(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"model":"test","choices":[{"finish_reason":"stop","message":{"content":"not json"}}]}'
        with patch("urllib.request.urlopen", return_value=Response()):
            with self.assertRaisesRegex(RuntimeError, "malformed JSON"):
                TemporalAnalyzer().infer([], 0, 8, {})

    def test_malformed_scene_rejected(self):
        self.video()
        r = self.client.put(
            "/api/videos/v/scene",
            json={
                "approved": True,
                "regions": [
                    {
                        "id": "x",
                        "label": "x",
                        "kind": "fence",
                        "points": [[0, 0], [1, 2]],
                    }
                ],
                "schedule": None,
            },
        )
        self.assertEqual(r.status_code, 400)

    def test_normalized_scene_polygon_is_accepted_and_out_of_range_is_rejected(self):
        valid = {"approved": False, "regions": [{"id": "gate", "label": "gate", "kind": "entrance", "points": [[0.1, 0.2], [0.3, 0.2], [0.3, 0.5]]}], "schedule": None}
        self.assertEqual(_valid_scene(valid)[0][0]["id"], "gate")
        invalid = {**valid, "regions": [{**valid["regions"][0], "points": [[0, 0], [1.1, 0], [0, 1]]}]}
        with self.assertRaises(ValueError):
            _valid_scene(invalid)

    def test_scene_allows_no_inferred_schedule(self):
        self.video()
        r = self.client.put(
            "/api/videos/v/scene",
            json={"approved": False, "regions": [], "schedule": None},
        )
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json["schedule"])
        bad = self.client.put(
            "/api/videos/v/scene",
            json={
                "approved": False,
                "regions": [],
                "schedule": {
                    "start": "7pm",
                    "end": "06:00",
                    "timezone": "America/Los_Angeles",
                },
            },
        )
        self.assertEqual(bad.status_code, 400)

    def test_raw_video_media_waits_for_normalized_mp4(self):
        vid = self.video("avi")
        raw = self.temp / "raw.avi"
        raw.write_bytes(b"not a video")
        self.store.run("UPDATE videos SET path=? WHERE id=?", (str(raw), vid))
        response = self.client.get(f"/api/videos/{vid}/media")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json["error"], "media is preparing")

    def test_relative_runtime_serves_range_frame_and_clip(self):
        relative = Path(os.path.relpath(self.temp, Path.cwd()))
        store = Store(relative)
        app = create_app(
            {
                "BEHAVIOR_STORE": store,
                "BEHAVIOR_ANALYZER": FakeAnalyzer(),
                "BEHAVIOR_START_WORKER": False,
            }
        )
        source = store.media / "range.mp4"
        source.write_bytes(b"x" * 200)
        frame = store.frames / "frame.jpg"
        frame.write_bytes(b"jpeg")
        clip = store.clips / "clip.mp4"
        clip.write_bytes(b"c" * 200)
        now = "2026-01-01T00:00:00Z"
        store.run(
            "INSERT INTO videos VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("range", "a.mp4", now, None, None, 1.0, "done", None, str(source), str(frame), "empty", 0, "[]", None, None, 0),
        )
        store.run(
            "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("clip", "range", 0.0, 1.0, "other_observable_event", "", "[]", "unknown", "[]", "unreviewed", 0, "", str(clip), "fake", "v1"),
        )
        client = app.test_client()
        media = client.get("/api/videos/range/media", headers={"Range": "bytes=0-99"})
        self.assertEqual(media.status_code, 206)
        self.assertEqual(len(media.data), 100)
        media.close()
        frame_response = client.get("/api/videos/range/frame")
        self.assertEqual(frame_response.status_code, 200)
        frame_response.close()
        clip_response = client.get("/api/events/clip/clip")
        self.assertEqual(clip_response.status_code, 200)
        clip_response.close()

    def test_cleanup_never_evicts_active_source(self):
        vid = self.video()
        source = self.temp / "a.mp4"
        source.write_bytes(b"x")
        self.store.run(
            "UPDATE videos SET path=?,status='processing' WHERE id=?",
            (str(source), vid),
        )
        self.store.system = lambda: {"used_percent": 90, "free_gb": 10}
        state = self.store.cleanup()
        self.assertTrue(state["paused"])
        self.assertTrue(source.exists())

    def test_cancel_and_restart_recovery(self):
        self.video()
        now = "2026-01-01T00:00:00Z"
        self.store.run(
            "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("j", "v", "analysis", "processing", 20, "work", None, now, now, 0),
        )
        # A new Store is the restart boundary and recovers interrupted work.
        recovered = Store(self.temp)
        self.assertEqual(
            recovered.one("SELECT status FROM jobs WHERE id='j'")[0], "queued"
        )
        self.client.post("/api/videos/v/cancel")
        self.assertEqual(
            self.store.one("SELECT cancelled FROM jobs WHERE id='j'")[0], 1
        )
        self.assertEqual(
            self.store.one("SELECT status FROM videos WHERE id='v'")[0], "done"
        )

    def test_queued_analysis_cancel_marks_video_cancelled(self):
        self.video()
        now = "2026-01-01T00:00:00Z"
        self.store.run("UPDATE videos SET status='queued' WHERE id='v'")
        self.store.run(
            "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("q", "v", "analysis", "queued", 0, "queued", None, now, now, 0),
        )
        response = self.client.post("/api/videos/v/cancel")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["video"]["status"], "cancelled")

    def test_stale_scene_failure_does_not_overwrite_approval(self):
        self.video()
        now = "2026-01-01T00:00:00Z"
        self.store.run(
            "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("s", "v", "scene", "queued", 0, "queued", None, now, now, 0),
        )

        class StaleFailure(FakeAnalyzer):
            def suggest_scene(inner, frame):
                self.store.run(
                    "UPDATE videos SET scene_status='approved',scene_approved=1,regions='[]',scene_version=scene_version+1 WHERE id='v'"
                )
                raise RuntimeError("model unavailable")

        self.app.config["BEHAVIOR_WORKER"].analyzer = StaleFailure()
        worker = self.app.config["BEHAVIOR_WORKER"]
        worker._scene(self.store.one("SELECT * FROM jobs WHERE id='s'"))
        scene = self.store.one(
            "SELECT scene_status,scene_approved FROM videos WHERE id='v'"
        )
        self.assertEqual((scene[0], scene[1]), ("approved", 1))

    def test_cancel_after_scene_model_call_never_publishes_geometry(self):
        self.video()
        now = "2026-01-01T00:00:00Z"
        self.store.run(
            "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("s", "v", "scene", "queued", 0, "queued", None, now, now, 0),
        )

        class Cancelling(FakeAnalyzer):
            def suggest_scene(inner, frame):
                self.store.run("UPDATE jobs SET cancelled=1 WHERE id='s'")
                return [
                    {
                        "id": "r",
                        "label": "gate",
                        "kind": "entrance",
                        "points": [[0, 0], [1, 0], [1, 1]],
                    }
                ]

        worker = self.app.config["BEHAVIOR_WORKER"]
        worker.analyzer = Cancelling()
        worker._scene(self.store.one("SELECT * FROM jobs WHERE id='s'"))
        self.assertEqual(
            self.store.one("SELECT regions FROM videos WHERE id='v'")[0], "[]"
        )
        self.assertEqual(
            self.store.one("SELECT status FROM jobs WHERE id='s'")[0], "cancelled"
        )

    def test_overlap_merge_and_outbox_dedupe(self):
        merged = _merge(
            [
                {
                    "start_s": 0,
                    "end_s": 6,
                    "action": "walking",
                    "description": "",
                    "evidence": [],
                    "uncertainty": "unknown",
                    "track_ids": ["1"],
                },
                {
                    "start_s": 4,
                    "end_s": 8,
                    "action": "Walking",
                    "description": "",
                    "evidence": [],
                    "uncertainty": "unknown",
                    "track_ids": ["2"],
                },
            ]
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["track_ids"], ["1", "2"])
        self.video()
        self.store.run(
            "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "e",
                "v",
                0,
                1,
                "walking",
                "",
                "[]",
                "unknown",
                "[]",
                "unreviewed",
                0,
                "",
                None,
                "fake",
                "v1",
            ),
        )
        self.store.run(
            "INSERT INTO outbox VALUES (?,?,?,?,?)",
            ("one", "e", "now", "pending_integration", "walking"),
        )
        self.store.run(
            "INSERT OR IGNORE INTO outbox VALUES (?,?,?,?,?)",
            ("two", "e", "now", "pending_integration", "walking"),
        )
        self.assertEqual(
            self.store.one("SELECT count(*) FROM outbox WHERE event_id='e'")[0], 1
        )

    def event(self, action, start, end):
        return {
            "start_s": start,
            "end_s": end,
            "action": action,
            "description": "",
            "evidence": [],
            "uncertainty": "unknown",
            "track_ids": [],
        }

    def test_merge_gap_joins_window_fragments_of_one_action(self):
        fragments = [
            self.event("access_interaction", 33.0, 33.5),
            self.event("access_interaction", 34.0, 34.5),
            self.event("access_interaction", 35.0, 35.5),
        ]
        self.assertEqual(len(_merge(fragments, gap=0)), 3)
        merged = _merge(fragments, gap=1.0)
        self.assertEqual(len(merged), 1)
        self.assertEqual((merged[0]["start_s"], merged[0]["end_s"]), (33.0, 35.5))

    def test_merge_gap_keeps_separated_and_differing_actions_apart(self):
        events = [
            self.event("access_interaction", 10.0, 11.0),
            self.event("access_interaction", 30.0, 31.0),
            self.event("climbing", 11.5, 12.0),
        ]
        merged = _merge(events, gap=1.0)
        self.assertEqual(len(merged), 3)
        self.assertEqual(
            [e["action"] for e in merged],
            ["access_interaction", "climbing", "access_interaction"],
        )

    def test_merge_gap_is_recorded_in_the_config_version(self):
        self.assertNotIn("gap", config_version())
        with patch.object(behavior, "MERGE_GAP_S", 1.0):
            self.assertEqual(config_version(), "temporal-v3-bounded-gap1")

    def test_focus_view_is_selectable_without_the_experimental_policy(self):
        self.assertEqual(behavior.EVENT_POLICY, "baseline")
        self.assertEqual(config_version(), "temporal-v3-bounded")
        with patch.object(behavior, "FOCUS_VIEW", True):
            self.assertEqual(config_version(), "temporal-v3-bounded-focus")
            reported = self.client.get("/api/system").get_json()
        self.assertEqual(reported["config_version"], "temporal-v3-bounded-focus")

    def test_focus_frames_reach_inference_and_are_explained_to_the_model(self):
        source = self.store.media / "v.mp4"
        source.write_bytes(b"media")
        self.video()
        self.store.run("UPDATE videos SET path=? WHERE id='v'", (str(source),))
        self.store.run(
            "INSERT INTO jobs VALUES ('j','v','analysis','queued',0,'queued',NULL,'now','now',0)"
        )
        sampled = self.temp / "000001.jpg"
        sampled.write_bytes(b"x")
        focused = self.temp / "000001-focus.jpg"
        focused.write_bytes(b"x")
        seen = []

        class Analyzer(FakeAnalyzer):
            def frames(self, path, out, start, end):
                return [sampled]

            def infer(self, frames, *args):
                seen.append(list(frames))
                return []

        worker = BehaviorWorker(self.store, Analyzer())
        with patch.object(behavior, "FOCUS_VIEW", True), patch(
            "behavior.views.context_detail_frames", return_value=[focused]
        ) as transform:
            worker.run_job("j")
        self.assertEqual(self.store.one("SELECT status FROM jobs WHERE id='j'")[0], "done")
        self.assertEqual(transform.call_args.args[0], [sampled])
        self.assertEqual(seen, [[focused], [focused]])

        prompts = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps(
                    {
                        "model": "test",
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {"content": '{"events":[]}'},
                            }
                        ],
                    }
                ).encode()

        def capture(req, timeout=None):
            prompts.append(json.loads(req.data)["messages"][0]["content"][0]["text"])
            return Response()

        with patch("urllib.request.urlopen", side_effect=capture):
            TemporalAnalyzer().infer([focused], 0, 8, {})
        self.assertIn("full scene LEFT, enlarged detail RIGHT", prompts[0])
        self.assertIn("Create events only for concrete", prompts[0])

    def test_confirmed_or_corrected_event_blocks_reanalysis(self):
        self.video()
        self.store.run(
            "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "e",
                "v",
                0,
                1,
                "walking",
                "",
                "[]",
                "unknown",
                "[]",
                "confirmed",
                0,
                "",
                None,
                "fake",
                "v1",
            ),
        )
        self.assertEqual(self.client.post("/api/videos/v/analyze").status_code, 409)
        self.store.run(
            "UPDATE events SET review_status='dismissed',correction='actually carrying a box' WHERE id='e'"
        )
        self.assertEqual(self.client.post("/api/videos/v/analyze").status_code, 409)


if __name__ == "__main__":
    unittest.main()
