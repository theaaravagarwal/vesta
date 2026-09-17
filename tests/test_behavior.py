import shutil
import tempfile
import unittest
import os
import json
import sqlite3
from contextlib import closing
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch
from evaluation.export_candidates import export as export_candidates

import behavior
from behavior import (
    BehaviorWorker,
    Store,
    TemporalAnalyzer,
    _merge,
    _has_non_routine_evidence,
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

    def test_inference_http_failure_preserves_server_status(self):
        error = HTTPError("http://inference", 500, "Internal Server Error", {}, BytesIO())
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "attempt=1.*http_status=500.*HTTP 500"):
                TemporalAnalyzer().infer([], 0, 8, {})

    def test_inference_timeout_margin_is_bounded_and_configurable(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(TemporalAnalyzer._request_timeout_s(), 180)
        with patch.dict(os.environ, {"BEHAVIOR_EVENT_REQUEST_TIMEOUT_S": "190"}):
            self.assertEqual(TemporalAnalyzer._request_timeout_s(), 190)
        with patch.dict(os.environ, {"BEHAVIOR_EVENT_REQUEST_TIMEOUT_S": "241"}):
            with self.assertRaisesRegex(RuntimeError, "between 1 and 240"):
                TemporalAnalyzer._request_timeout_s()

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

    def test_tailscale_access_is_disabled_by_default_for_local_development(self):
        self.assertEqual(self.client.get("/review").status_code, 200)

    def test_tailscale_access_denies_missing_wrong_and_non_loopback_identity(self):
        app = create_app(
            {
                "BEHAVIOR_STORE": Store(self.temp / "secured"),
                "BEHAVIOR_ANALYZER": FakeAnalyzer(),
                "BEHAVIOR_START_WORKER": False,
                "BEHAVIOR_TAILSCALE_AUTH": True,
                "BEHAVIOR_TAILSCALE_ALLOWED_LOGIN": "aarav",
                "BEHAVIOR_TAILSCALE_CANONICAL_ORIGIN": "https://vesta.mesh.example",
            }
        )
        client = app.test_client()
        self.assertEqual(client.get("/review").status_code, 403)
        self.assertEqual(
            client.get("/review", headers={"Tailscale-User-Login": "someone-else"}).status_code,
            403,
        )
        self.assertEqual(
            client.get(
                "/review",
                headers={
                    "Tailscale-User-Login": "aarav",
                    "X-Forwarded-For": "127.0.0.1",
                },
                environ_base={"REMOTE_ADDR": "100.64.0.2"},
            ).status_code,
            403,
        )

    def test_tailscale_access_allows_only_exact_identity_from_loopback_for_every_route(self):
        app = create_app(
            {
                "BEHAVIOR_STORE": Store(self.temp / "secured-media"),
                "BEHAVIOR_ANALYZER": FakeAnalyzer(),
                "BEHAVIOR_START_WORKER": False,
                "BEHAVIOR_TAILSCALE_AUTH": True,
                "BEHAVIOR_TAILSCALE_ALLOWED_LOGIN": "aarav",
                "BEHAVIOR_TAILSCALE_CANONICAL_ORIGIN": "https://vesta.mesh.example",
            }
        )
        client = app.test_client()
        headers = {"Tailscale-User-Login": "aarav"}
        self.assertEqual(client.get("/review", headers=headers).status_code, 200)
        # A missing identity is denied before a route can disclose whether its
        # media object exists.
        self.assertEqual(client.get("/api/videos/not-a-video/media").status_code, 403)
        self.assertEqual(client.get("/api/videos/not-a-video/media", headers=headers).status_code, 404)
        self.assertEqual(client.get("/static/review.js").status_code, 403)

    def test_tailscale_access_rejects_cross_origin_mutations_but_allows_authenticated_cli(self):
        app = create_app(
            {
                "BEHAVIOR_STORE": Store(self.temp / "secured-origin"),
                "BEHAVIOR_ANALYZER": FakeAnalyzer(),
                "BEHAVIOR_START_WORKER": False,
                "BEHAVIOR_TAILSCALE_AUTH": True,
                "BEHAVIOR_TAILSCALE_ALLOWED_LOGIN": "aarav",
                "BEHAVIOR_TAILSCALE_CANONICAL_ORIGIN": "https://vesta.mesh.example",
            }
        )
        client = app.test_client()
        headers = {"Tailscale-User-Login": "aarav"}
        self.assertEqual(
            client.post("/api/videos/not-a-video/cancel", headers={**headers, "Origin": "https://other.example"}).status_code,
            403,
        )
        self.assertEqual(
            client.post("/api/videos/not-a-video/cancel", headers={**headers, "Origin": "https://vesta.mesh.example"}).status_code,
            404,
        )
        self.assertEqual(client.post("/api/videos/not-a-video/cancel", headers=headers).status_code, 404)

    def test_tailscale_access_requires_complete_valid_production_configuration(self):
        with self.assertRaisesRegex(ValueError, "ALLOWED_LOGIN"):
            create_app({"BEHAVIOR_TAILSCALE_AUTH": True, "BEHAVIOR_START_WORKER": False})
        with self.assertRaisesRegex(ValueError, "CANONICAL_ORIGIN"):
            create_app(
                {
                    "BEHAVIOR_TAILSCALE_AUTH": True,
                    "BEHAVIOR_TAILSCALE_ALLOWED_LOGIN": "aarav",
                    "BEHAVIOR_START_WORKER": False,
                }
            )

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
            self.assertEqual(config_version(), "temporal-v3-bounded-gap1-evidence2")

    def test_focus_view_is_selectable_without_the_experimental_policy(self):
        self.assertEqual(behavior.EVENT_POLICY, "baseline")
        self.assertEqual(config_version(), "temporal-v3-bounded-evidence2")
        with patch.object(behavior, "FOCUS_VIEW", True):
            self.assertEqual(config_version(), "temporal-v3-bounded-focus-evidence2")
            reported = self.client.get("/api/system").get_json()
        self.assertEqual(reported["config_version"], "temporal-v3-bounded-focus-evidence2")

    def test_vehicle_presence_is_not_an_alert_without_forceful_evidence(self):
        event = {
            "action": "access_interaction",
            "description": "A person is near a car, possibly opening a door.",
            "evidence": ["A person stands beside the car."],
            "uncertainty": "The interaction is unclear.",
        }
        self.assertFalse(_has_non_routine_evidence(event))
        event["evidence"] = ["The person pries the car door with a tool."]
        self.assertTrue(_has_non_routine_evidence(event))
        event["action"] = "climbing"
        event["evidence"] = ["The person climbs a fence beside a car."]
        self.assertTrue(_has_non_routine_evidence(event))
        event["action"] = "other_observable_event"
        event["evidence"] = ["A person falls beside the car."]
        self.assertTrue(_has_non_routine_evidence(event))

    def test_action_labels_need_matching_physical_evidence(self):
        event = {
            "action": "boundary_entry", "description": "A person enters the scene",
            "evidence": ["A person walks into the frame"], "uncertainty": "",
        }
        self.assertFalse(_has_non_routine_evidence(event))
        event["evidence"] = ["The person climbs over the fence."]
        self.assertTrue(_has_non_routine_evidence(event))
        event["action"] = "object_tampering"
        event["evidence"] = ["A person stands beside a motorcycle."]
        self.assertFalse(_has_non_routine_evidence(event))
        event["evidence"] = ["The person cuts a chain on the motorcycle."]
        self.assertTrue(_has_non_routine_evidence(event))

    def test_candidate_trace_keeps_rejected_and_accepted_model_outputs(self):
        source = self.store.media / "v.mp4"
        source.write_bytes(b"media")
        self.video()
        self.store.run("UPDATE videos SET path=?,duration_s=8 WHERE id='v'", (str(source),))
        self.store.run("INSERT INTO jobs VALUES ('j','v','analysis','queued',0,'queued',NULL,'now','now',0)")
        sampled = self.temp / "000001.jpg"
        sampled.write_bytes(b"frame")

        class Analyzer(FakeAnalyzer):
            def frames(self, *args):
                return [sampled]

            def infer(self, *args):
                return [
                    {"start_s": 0, "end_s": 1, "action": "other_observable_event",
                     "description": "A person stands near a car", "evidence": ["Person stands beside car"], "uncertainty": ""},
                    {"start_s": 1, "end_s": 2, "action": "climbing",
                     "description": "A person climbs over a fence", "evidence": ["Leg moves over fence"], "uncertainty": ""},
                ]

        worker = BehaviorWorker(self.store, Analyzer())
        with patch.object(worker, "_save_event") as save:
            worker.run_job("j")
        self.assertEqual(self.store.one("SELECT status FROM jobs WHERE id='j'")[0], "done")
        save.assert_called_once()
        trace = export_candidates(self.store.db_path, "v")
        self.assertEqual(trace["windows"][0]["candidate_count"], 2)
        self.assertEqual(trace["windows"][0]["frame_count"], 1)
        self.assertEqual([c["decision"] for c in trace["candidates"]], ["rejected", "accepted"])
        self.assertEqual(trace["candidates"][0]["reason"], "no_specific_physical_incident")

    def _analysis_job(self, analyzer, duration=12.0):
        source = self.store.media / "v.mp4"
        source.write_bytes(b"media")
        self.video()
        self.store.run("UPDATE videos SET path=?,duration_s=? WHERE id='v'", (str(source), duration))
        self.store.run("INSERT INTO jobs VALUES ('j','v','analysis','queued',0,'queued',NULL,'now','now',0)")
        return BehaviorWorker(self.store, analyzer)

    def test_old_analysis_window_schema_migrates_twice_and_preserves_row(self):
        root = self.temp / "old-store"
        root.mkdir()
        db = root / "behavior.sqlite3"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE analysis_windows (job_id TEXT,video_id TEXT,window_index INTEGER,start_s REAL,end_s REAL,frame_count INTEGER,status TEXT,candidate_count INTEGER DEFAULT 0,PRIMARY KEY(job_id,window_index))")
        conn.execute("INSERT INTO analysis_windows VALUES ('j','v',0,0,1,2,'done',0)")
        conn.commit(); conn.close()
        Store(root)
        Store(root)
        with closing(sqlite3.connect(db)) as conn:
            row = conn.execute("SELECT frame_count,status,candidate_count,model,config_version FROM analysis_windows").fetchone()
        self.assertEqual(row, (2, "done", 0, None, None))

    def test_sampler_failure_creates_error_window_with_zero_frames(self):
        class SamplerFailure(FakeAnalyzer):
            def frames(self, *args):
                raise RuntimeError("sampler exploded")
        worker = self._analysis_job(SamplerFailure())
        worker.run_job("j")
        row = self.store.one("SELECT status,frame_count FROM analysis_windows WHERE job_id='j'")
        self.assertEqual(tuple(row), ("error", 0))

    def test_malformed_model_output_marks_current_window_and_preserves_done_window(self):
        sampled = self.temp / "sample.jpg"
        sampled.write_bytes(b"frame")
        class Malformed(FakeAnalyzer):
            def frames(self, *args):
                return [sampled]
            def infer(self, *args):
                if args[1] < 1:
                    return []
                return [{}]
        worker = self._analysis_job(Malformed())
        worker.run_job("j")
        rows = self.store.all("SELECT window_index,status,candidate_count FROM analysis_windows ORDER BY window_index")
        self.assertEqual([tuple(row) for row in rows], [(0, "done", 0), (1, "error", 1)])

    def test_zero_candidate_window_records_model_and_config_provenance(self):
        sampled = self.temp / "sample.jpg"
        sampled.write_bytes(b"frame")
        class Empty(FakeAnalyzer):
            model_name = "provenance-model"
            def frames(self, *args):
                return [sampled]
        worker = self._analysis_job(Empty(), duration=8.0)
        worker.run_job("j")
        row = self.store.one("SELECT status,candidate_count,model,config_version FROM analysis_windows")
        self.assertEqual(row[0], "done")
        self.assertEqual(row[1], 0)
        self.assertEqual(row[2], "provenance-model")
        self.assertEqual(row[3], behavior.CONFIG_VERSION)

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
