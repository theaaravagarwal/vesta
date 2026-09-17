"""Focused fixed-camera pilot behavior tests; no camera or model is opened."""

from __future__ import annotations

import shutil
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

from behavior import Store, create_app
from behavior.boundary import BoundaryCollectorService, CameraSecrets, Crossing, CrossingEngine


class FakeAnalyzer:
    model_name = "fake-vlm"


class BoundaryTests(unittest.TestCase):
    camera = "cam_boundary12345678"

    def setUp(self):
        import cv2
        import numpy as np
        self.temp = Path(tempfile.mkdtemp())
        self.jpeg = bytes(cv2.imencode(".jpg", np.zeros((32, 32, 3), dtype=np.uint8))[1])
        self.store = Store(self.temp)
        self.app = create_app({
            "BEHAVIOR_STORE": self.store,
            "BEHAVIOR_ANALYZER": FakeAnalyzer(),
            "BEHAVIOR_START_WORKER": False,
            "BEHAVIOR_BOUNDARY_SECRETS_DIR": self.temp / "secrets",
        })
        self.client = self.app.test_client()

    def tearDown(self):
        shutil.rmtree(self.temp, ignore_errors=True)

    @staticmethod
    def line():
        return {"label": "Gate", "start": [0.2, 0.5], "end": [0.8, 0.5],
                "direction": "either", "enabled": True}

    def calibrated(self):
        self.store.boundary_ensure_camera(self.camera)
        preview = self.store.save_boundary_preview(self.camera, b"jpeg")
        revision = self.store.boundary_create_revision(self.camera, preview, [self.line()])
        self.store.boundary_approve_revision(self.camera, revision["id"])
        return revision

    def test_finite_line_hysteresis_and_directional_dedupe(self):
        line = self.line() | {"id": "line_boundary12345678"}
        engine = CrossingEngine([line], hysteresis=0.02, dedupe_s=3)
        self.assertEqual(engine.update([{"track_id": "7", "ground_point": [0.5, 0.47]}], 0), [])
        # In the hysteresis band: retains the last stable side.
        self.assertEqual(engine.update([{"track_id": "7", "ground_point": [0.5, 0.51]}], 0.2), [])
        first = engine.update([{"track_id": "7", "ground_point": [0.5, 0.56]}], 0.4)
        self.assertEqual([item.direction for item in first], ["a_to_b"])
        # A real opposite-direction return is distinct, even inside cooldown.
        reverse = engine.update([{"track_id": "7", "ground_point": [0.5, 0.44]}], 0.6)
        self.assertEqual([item.direction for item in reverse], ["b_to_a"])
        # A same-direction oscillation is deduplicated.
        self.assertEqual(engine.update([{"track_id": "7", "ground_point": [0.5, 0.56]}], 0.8), [])
        # Crossing an extension beyond the finite segment is ignored.
        self.assertEqual(engine.update([{"track_id": "8", "ground_point": [0.98, 0.44]}], 1), [])
        self.assertEqual(engine.update([{"track_id": "8", "ground_point": [0.98, 0.56]}], 2), [])

    def test_setup_and_start_do_not_claim_capture_before_sidecar_heartbeat(self):
        self.client.put(f"/api/cameras/{self.camera}", json={"name": "North gate"})
        response = self.client.post(f"/api/cameras/{self.camera}/setup")
        self.assertEqual(response.status_code, 409)
        revision = self.calibrated()
        response = self.client.post(f"/api/cameras/{self.camera}/collector/start")
        self.assertEqual(response.status_code, 409)  # no local credential file
        self.assertEqual(self.client.post(
            f"/api/cameras/{self.camera}/reference-views/{revision['id']}/approve"
        ).status_code, 200)

    def test_config_is_immutable_revision_backed_and_pauses_on_new_view(self):
        self.store.boundary_ensure_camera(self.camera)
        preview = self.store.save_boundary_preview(self.camera, b"first")
        first = self.store.boundary_create_revision(self.camera, preview, [self.line()])
        self.store.boundary_approve_revision(self.camera, first["id"])
        second_preview = self.store.save_boundary_preview(self.camera, b"second")
        response = self.client.post(f"/api/cameras/{self.camera}/reference-views", json={"preview_id": second_preview})
        self.assertEqual(response.status_code, 201)
        data = response.json["camera"]
        self.assertEqual(data["calibration_state"], "needs_approval")
        self.assertEqual(data["desired_state"], "stopped")
        self.assertEqual(data["active_revision_id"], first["id"])
        self.assertEqual(len(data["revisions"]), 2)

    def test_candidate_is_independent_of_vlm_ack_is_explicit_and_sessions_are_durable(self):
        revision = self.calibrated()
        line_id = self.store.boundary_camera(self.camera)["lines"][0]["id"]
        session = "bsess_" + "a" * 32
        self.store.boundary_session_start(session, self.camera, 1, revision["id"])
        event_id = self.store.create_boundary_event(
            camera_id=self.camera, session_id=session, epoch=1, sequence=12, revision_id=revision["id"],
            crossing=Crossing(line_id, "42", "a_to_b", 1759999999.8, 1760000000.0, (0.5, 0.6)),
            pre_frames=[self.jpeg], post_frames=[self.jpeg], tracker_model="local.pt",
        )
        self.store.boundary_session_end(session, "stopped")
        event = self.store.one("SELECT review_status,model FROM events WHERE id=?", (event_id,))
        self.assertEqual(tuple(event), ("unreviewed", "local.pt"))
        provenance = self.store.boundary_event_provenance(event_id)
        self.assertIsNone(provenance["dashboard_render_ack_received_at_s"])
        ack = self.client.post(f"/api/cameras/{self.camera}/events/{event_id}/rendered")
        self.assertEqual(ack.status_code, 200)
        self.assertIsNotNone(ack.json["dashboard_render_ack_received_at_s"])
        again = self.client.post(f"/api/cameras/{self.camera}/events/{event_id}/rendered")
        self.assertEqual(again.json["dashboard_render_ack_received_at_s"], ack.json["dashboard_render_ack_received_at_s"])
        session_data = self.client.get(f"/api/cameras/{self.camera}/sessions/{session}").json
        self.assertEqual(session_data["alerts"][0]["id"], event_id)
        self.assertEqual(session_data["alerts"][0]["exposure"], "boundary_line_crossing")

    def test_seven_day_retention_preserves_pinned_session_evidence(self):
        revision = self.calibrated()
        line_id = self.store.boundary_camera(self.camera)["lines"][0]["id"]
        session = "bsess_" + "b" * 32
        self.store.boundary_session_start(session, self.camera, 0, revision["id"])
        event_id = self.store.create_boundary_event(
            camera_id=self.camera, session_id=session, epoch=0, sequence=1, revision_id=revision["id"],
            crossing=Crossing(line_id, "1", "a_to_b", 1759999999.8, 1760000000.0, (0.5, 0.6)),
            pre_frames=[self.jpeg], post_frames=[self.jpeg], tracker_model="local.pt",
        )
        self.store.boundary_session_end(session, "stopped")
        self.store.run("UPDATE boundary_sessions SET ended_at='2026-01-01T00:00:00Z' WHERE id=?", (session,))
        self.store.run("UPDATE events SET pinned=1 WHERE id=?", (event_id,))
        now = datetime(2026, 1, 10, tzinfo=timezone.utc)
        self.assertEqual(self.store.cleanup_boundary_retention(now=now), {"events": 0, "sessions": 1})
        video_id = self.store.one("SELECT video_id FROM events WHERE id=?", (event_id,))["video_id"]
        self.assertEqual(self.store.one("SELECT status FROM videos WHERE id=?", (video_id,))["status"], "done")
        self.store.run("UPDATE events SET pinned=0 WHERE id=?", (event_id,))
        self.assertEqual(self.store.cleanup_boundary_retention(now=now), {"events": 1, "sessions": 0})
        self.assertIsNotNone(self.store.one("SELECT 1 FROM events WHERE id=?", (event_id,)))
        self.assertEqual(self.store.one("SELECT status FROM videos WHERE id=?", (video_id,))["status"], "evicted")
        self.assertEqual(self.store.one("SELECT state FROM boundary_sessions WHERE id=?", (session,))["state"], "expired")

    def test_physical_view_drift_pauses_before_any_later_crossing(self):
        """A shifted frame stops tracking without requiring a new API revision."""
        import cv2
        import numpy as np

        rng = np.random.default_rng(44)
        reference = rng.integers(0, 255, (320, 480, 3), dtype=np.uint8)
        for index in range(24):
            cv2.circle(reference, (20 + (index * 37) % 440, 20 + (index * 53) % 280), 6,
                       (255, 255, 255), -1)
        ok, original = cv2.imencode(".jpg", reference)
        self.assertTrue(ok)
        self.store.boundary_ensure_camera(self.camera)
        preview = self.store.save_boundary_preview(self.camera, bytes(original))
        revision = self.store.boundary_create_revision(self.camera, preview, [self.line()])
        self.store.boundary_approve_revision(self.camera, revision["id"])
        shifted = cv2.warpAffine(reference, np.float32([[1, 0, 80], [0, 1, 0]]), (480, 320))
        aligned, reason = self.store.boundary_reference_alignment(self.camera, bytes(cv2.imencode(".jpg", shifted)[1]))
        self.assertFalse(aligned, reason)

        class Capture:
            def read(self): return True, shifted
            def release(self): pass
        class Tracker:
            called = 0
            def track(self, *_args, **_kwargs):
                self.called += 1
                return []
        tracker = Tracker()
        model = self.temp / "local.pt"
        model.write_bytes(b"local only")
        service = BoundaryCollectorService(self.store, CameraSecrets(self.temp / "secrets"), model_path=str(model),
                                           tracker_factory=lambda _path: tracker)
        service._open = lambda _camera: Capture()  # type: ignore[method-assign]
        self.store.boundary_set_desired(self.camera, "running")
        service._capture_loop(self.camera, threading.Event())
        camera = self.store.boundary_camera(self.camera)
        self.assertEqual(tracker.called, 0)
        self.assertEqual(camera["calibration_state"], "needs_approval")
        self.assertEqual(camera["desired_state"], "stopped")
        self.assertTrue(camera["collector"]["alerts_paused"])
        self.assertEqual(camera["collector"]["state"], "paused")

    def test_registration_creates_only_a_manual_proposal(self):
        import cv2
        import numpy as np

        rng = np.random.default_rng(88)
        reference = rng.integers(0, 255, (300, 440, 3), dtype=np.uint8)
        ok, encoded = cv2.imencode(".jpg", reference)
        self.assertTrue(ok)
        self.store.boundary_ensure_camera(self.camera)
        initial_preview = self.store.save_boundary_preview(self.camera, bytes(encoded))
        initial = self.store.boundary_create_revision(self.camera, initial_preview, [self.line()])
        self.store.boundary_approve_revision(self.camera, initial["id"])
        shifted = cv2.warpAffine(reference, np.float32([[1, 0, 8], [0, 1, 0]]), (440, 300))
        moved_preview = self.store.save_boundary_preview(self.camera, bytes(cv2.imencode(".jpg", shifted)[1]))
        self.store.boundary_request_registration(self.camera, moved_preview)
        result = self.store.boundary_complete_registration(self.camera, moved_preview)
        camera = self.store.boundary_camera(self.camera)
        self.assertEqual(result["revision"]["status"], "proposed")
        self.assertEqual(camera["active_revision_id"], initial["id"])
        self.assertEqual(camera["calibration_state"], "needs_approval")
        self.assertEqual(camera["desired_state"], "stopped")
        self.assertEqual(camera["registration"]["state"], "proposed")


if __name__ == "__main__":
    unittest.main()
