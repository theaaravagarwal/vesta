"""Independent behavioral checks for the fixed-camera crossing engine."""
import unittest

from behavior.boundary import CrossingEngine


LINE = {
    "id": "line_test01",
    "label": "test line",
    "start": [0.5, 0.2],
    "end": [0.5, 0.8],
    "direction": "either",
    "enabled": True,
}


def obs(track, x, y=0.5):
    return {"track_id": track, "ground_point": [x, y]}


class BoundaryCrossingIntegrationTests(unittest.TestCase):
    def test_sustained_crossing_emits_one_directional_event(self):
        engine = CrossingEngine([LINE], dedupe_s=3)
        self.assertEqual(engine.update([obs("t1", 0.35)], 0.0), [])
        self.assertEqual(engine.update([obs("t1", 0.65)], 1.0)[0].direction, "b_to_a")
        self.assertEqual(engine.update([obs("t1", 0.70)], 1.2), [])

    def test_approach_and_turnback_without_crossing_is_silent(self):
        engine = CrossingEngine([LINE])
        for timestamp, x in enumerate((0.35, 0.43, 0.47, 0.43, 0.36)):
            self.assertEqual(engine.update([obs("t1", x)], timestamp), [])

    def test_parallel_motion_does_not_cross(self):
        engine = CrossingEngine([LINE])
        for timestamp, y in enumerate((0.3, 0.4, 0.6, 0.7)):
            self.assertEqual(engine.update([obs("t1", 0.35, y)], timestamp), [])

    def test_hysteresis_suppresses_line_jitter(self):
        engine = CrossingEngine([LINE], hysteresis=0.015)
        for timestamp, x in enumerate((0.49, 0.51, 0.49, 0.51, 0.49)):
            self.assertEqual(engine.update([obs("t1", x)], timestamp), [])

    def test_crossing_outside_finite_line_segment_is_ignored(self):
        engine = CrossingEngine([LINE], endpoint_margin=0.02)
        self.assertEqual(engine.update([obs("t1", 0.35, 0.05)], 0), [])
        self.assertEqual(engine.update([obs("t1", 0.65, 0.05)], 1), [])

    def test_direction_filter_rejects_wrong_direction_without_losing_side_state(self):
        restricted = dict(LINE, direction="a_to_b")
        engine = CrossingEngine([restricted])
        self.assertEqual(engine.update([obs("t1", 0.35)], 0), [])
        self.assertEqual(engine.update([obs("t1", 0.65)], 1), [])
        # Returning across the line is the allowed direction and should emit.
        crosses = engine.update([obs("t1", 0.35)], 2)
        self.assertEqual(len(crosses), 1)
        self.assertEqual(crosses[0].direction, "a_to_b")

    def test_gap_reset_prevents_stale_track_state_from_creating_crossing(self):
        engine = CrossingEngine([LINE])
        engine.update([obs("t1", 0.35)], 0)
        # Collector performs this reset after a stream gap/reconnect.
        engine.reset()
        self.assertEqual(engine.update([obs("t1", 0.65)], 100), [])

    def test_new_track_must_be_seen_on_both_sides_before_crossing(self):
        engine = CrossingEngine([LINE])
        self.assertEqual(engine.update([obs("new", 0.65)], 0), [])
        self.assertEqual(engine.update([obs("new", 0.70)], 1), [])
        self.assertEqual(engine.update([obs("other", 0.35)], 2), [])

    def test_distinct_return_crossing_is_not_suppressed_by_global_dedupe(self):
        engine = CrossingEngine([LINE], dedupe_s=3)
        engine.update([obs("t1", 0.35)], 0)
        first = engine.update([obs("t1", 0.65)], 1)
        self.assertEqual(len(first), 1)
        return_crossing = engine.update([obs("t1", 0.35)], 2)
        self.assertEqual(len(return_crossing), 1)
        self.assertEqual(return_crossing[0].direction, "a_to_b")


if __name__ == "__main__":
    unittest.main()

class _Tensor:
    def __init__(self, value):
        self.value = value
    def cpu(self):
        return self
    def tolist(self):
        return self.value
    def int(self):
        return self


class _Boxes:
    def __init__(self, box, track_id):
        self.xyxy = _Tensor([box])
        self.id = _Tensor([track_id])
        self.cls = _Tensor([0])


class _Result:
    def __init__(self, box, track_id):
        self.boxes = _Boxes(box, track_id)


class _Tracker:
    def __init__(self, boxes):
        self.boxes = iter(boxes)
    def track(self, frame, **kwargs):
        return [_Result(next(self.boxes), 7)]


class _Capture:
    def __init__(self, frames, stop_event):
        self.frames = iter(frames)
        self.stop_event = stop_event
    def read(self):
        try:
            return True, next(self.frames)
        except StopIteration:
            self.stop_event.set()
            return False, None
    def release(self):
        pass


class BoundaryCollectorPipelineTests(unittest.TestCase):
    def test_injected_tracker_writes_encoded_evidence_and_provenance(self):
        import shutil
        import tempfile
        from pathlib import Path
        import cv2
        import numpy as np
        from behavior import Store
        from behavior.boundary import BoundaryCollectorService, CameraSecrets

        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        store = Store(root)
        camera_id = "cam_pipeline01"
        store.boundary_ensure_camera(camera_id)
        # The collector now verifies fixed-view geometry before publishing a
        # candidate, so use one feature-bearing reference throughout this
        # synthetic stream rather than an unreadable placeholder file.
        rng = np.random.default_rng(7)
        reference_frame = rng.integers(0, 255, (96, 160, 3), dtype=np.uint8)
        ok, reference_encoded = cv2.imencode(".jpg", reference_frame)
        self.assertTrue(ok)
        preview = root / "reference.jpg"
        preview.write_bytes(bytes(reference_encoded))
        preview_id = "preview_" + "a" * 16
        store.run("INSERT INTO boundary_previews VALUES (?,?,?,?)", (preview_id, camera_id, str(preview), "2026-01-01T00:00:00Z"))
        line = {"id": "line_pipeline", "label": "gate", "start": [0.5, 0.2], "end": [0.5, 0.8], "direction": "either", "enabled": True}
        revision = store.boundary_create_revision(camera_id, preview_id, [line])
        store.boundary_approve_revision(camera_id, revision["id"])
        store.boundary_set_desired(camera_id, "running")
        model = root / "tracker.onnx"
        model.write_bytes(b"injected")
        frames = [reference_frame.copy() for _ in range(6)]
        boxes = [[40, 25, 56, 48], [40, 25, 56, 48], [40, 25, 56, 48],
                 [104, 25, 120, 48], [104, 25, 120, 48], [104, 25, 120, 48]]
        service = BoundaryCollectorService(store, CameraSecrets(root), model_path=str(model),
                                           tracker_factory=lambda _: _Tracker(boxes), pre_frames=1, post_frames=1)
        cancelled = __import__("threading").Event()
        service._open = lambda _: _Capture(frames, cancelled)
        service._capture_loop(camera_id, cancelled)
        event = store.one("SELECT * FROM events WHERE action='boundary_entry'")
        self.assertIsNotNone(event)
        provenance = store.boundary_event_provenance(event["id"])
        self.assertEqual(provenance["line_id"], line["id"])
        self.assertEqual(provenance["track_id"], "7")
        self.assertTrue(provenance["pre_frames"])
        self.assertTrue(provenance["post_frames"])
        for url in provenance["pre_frames"] + provenance["post_frames"]:
            self.assertTrue((root / "boundary" / "evidence" / event["id"] / url.rsplit("/", 1)[-1]).is_file())
        self.assertEqual(store.one("SELECT count(*) AS n FROM boundary_event_provenance")["n"], 1)

class BoundaryHttpContractTests(unittest.TestCase):
    def setUp(self):
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from behavior import Store, create_app
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, True)
        (self.temp / "camera-secrets").mkdir()
        secret = self.temp / "camera-secrets" / "cam_http0001.json"
        secret.write_text(json.dumps({"rtsp_url": "rtsp://127.0.0.1/example"}))
        secret.chmod(0o600)
        self.store = Store(self.temp)
        self.app = create_app({"BEHAVIOR_STORE": self.store, "BEHAVIOR_START_WORKER": False})
        self.client = self.app.test_client()
        self.camera = "cam_http0001"

    def test_setup_reference_approve_start_health_and_render_ack_are_scoped_and_idempotent(self):
        import time
        preview_id = "preview_" + "b" * 16
        preview = self.temp / "reference.jpg"
        preview.write_bytes(b"jpg")
        self.store.boundary_ensure_camera(self.camera)
        self.store.run("INSERT INTO boundary_previews VALUES (?,?,?,?)", (preview_id, self.camera, str(preview), "2026-01-01T00:00:00Z"))
        self.assertEqual(self.client.put(f"/api/cameras/{self.camera}", json={"name": "Test"}).status_code, 200)
        self.assertEqual(self.client.post(f"/api/cameras/{self.camera}/setup").status_code, 202)
        line = {"id": "line_http0001", "label": "gate", "start": [0.5, 0.2], "end": [0.5, 0.8], "direction": "either", "enabled": True}
        response = self.client.post(f"/api/cameras/{self.camera}/reference-views", json={"preview_id": preview_id, "lines": [line]})
        self.assertEqual(response.status_code, 201)
        revision_id = response.json["revision"]["id"]
        self.assertEqual(self.client.post(f"/api/cameras/{self.camera}/reference-views/{revision_id}/approve").status_code, 200)
        test_started = self.client.post(f"/api/cameras/{self.camera}/test-sessions/start", json={"name": "pilot", "split": "held_out"})
        self.assertEqual(test_started.status_code, 202)
        test_session = test_started.json["session"]
        self.assertEqual(test_session["split"], "held_out")
        self.assertIsNone(test_session["ordinary_exposure_s"])
        test_id = test_session["id"]
        expected = {"line_id": line["id"], "start_s": 10, "end_s": 15, "direction": "b_to_a"}
        self.assertEqual(self.client.post(f"/api/cameras/{self.camera}/test-sessions/{test_id}/expected-crossings", json=expected).status_code, 201)
        self.assertEqual(self.client.post(f"/api/cameras/{self.camera}/test-sessions/{test_id}/ordinary-intervals", json={"start_s": 12, "end_s": 20}).status_code, 400)
        self.assertEqual(self.client.post(f"/api/cameras/{self.camera}/test-sessions/{test_id}/ordinary-intervals", json={"start_s": 20, "end_s": 80}).status_code, 201)
        self.assertIsNone(self.client.get(f"/api/cameras/{self.camera}/test-sessions/{test_id}").json["ordinary_exposure_s"])
        self.assertEqual(self.client.post(f"/api/cameras/{self.camera}/test-sessions/{test_id}/freeze").status_code, 409)
        self.store.run("UPDATE boundary_sessions SET state='stopped', ended_at=? WHERE id=?", ("2026-01-01T01:00:00Z", test_id))
        self.assertEqual(self.client.post(f"/api/cameras/{self.camera}/test-sessions/{test_id}/freeze").status_code, 200)
        self.assertEqual(self.client.post(f"/api/cameras/{self.camera}/test-sessions/{test_id}/ordinary-intervals", json={"start_s": 90, "end_s": 100}).status_code, 409)
        started = self.client.post(f"/api/cameras/{self.camera}/collector/start")
        self.assertEqual(started.status_code, 202)
        self.assertEqual(started.json["desired_state"], "running")
        self.assertEqual(started.json["collector"]["state"], "unavailable")  # desired state is not a false heartbeat claim

        from behavior.boundary import Crossing
        import cv2
        import numpy as np
        encoded, frame_bytes = cv2.imencode(".jpg", np.zeros((16, 16, 3), dtype=np.uint8))
        self.assertTrue(encoded)
        self.store.boundary_session_start("bsess_http01", self.camera, 1, revision_id)
        crossing = Crossing(line["id"], "track1", "b_to_a", time.time() - 1, time.time(), (0.6, 0.5))
        event_id = self.store.create_boundary_event(camera_id=self.camera, session_id="bsess_http01", epoch=1, sequence=1, revision_id=revision_id, crossing=crossing, pre_frames=[bytes(frame_bytes)], post_frames=[bytes(frame_bytes)], tracker_model="test")
        ack = self.client.post(f"/api/cameras/{self.camera}/events/{event_id}/rendered")
        self.assertEqual(ack.status_code, 200)
        first = ack.json["dashboard_render_ack_received_at_s"]
        second = self.client.post(f"/api/cameras/{self.camera}/events/{event_id}/rendered").json["dashboard_render_ack_received_at_s"]
        self.assertEqual(first, second)
        self.assertEqual(self.client.post(f"/api/cameras/cam_other01/events/{event_id}/rendered").status_code, 404)
        exported = self.client.get(f"/api/cameras/{self.camera}/sessions/bsess_http01")
        self.assertEqual(exported.status_code, 200)
        self.assertEqual(exported.json["alerts"][0]["dashboard_render_ack_received_at_s"], first)
        # Candidate alerts never become expected truth automatically.
        self.assertEqual(exported.json["staged_crossings"], [])
        from evaluation.pilot import score_pilot_session
        self.assertFalse(score_pilot_session(exported.json)["passed"])
