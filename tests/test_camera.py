"""Camera chunk admission is bounded before a worker can consume it."""

from __future__ import annotations

import io
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from behavior import Store, create_app
from behavior.capture import CameraAdmissionFull, CameraQueue, CameraQueueLimits


class FakeAnalyzer:
    model_name = "camera-fake"

    def metadata(self, path):
        return 8.0

    def track_video(self, *args):
        return []

    def frames(self, path, folder, start, end):
        folder.mkdir(parents=True, exist_ok=True)
        frame = folder / "000001.jpg"
        frame.write_bytes(b"frame")
        return [frame]

    def infer(self, *args):
        return [{
            "start_s": 1.0,
            "end_s": 2.0,
            "action": "climbing",
            "description": "A person climbs over a visible fence.",
            "evidence": ["Frames show a person moving over the fence."],
            "uncertainty": "The far side is outside this camera view.",
        }]

    def suggest_scene(self, frame):
        return []


def write_ffmpeg_output(command, **kwargs):
    """Deterministic stand-in: worker tests cover pipeline ownership, not ffmpeg."""
    Path(command[-1]).write_bytes(b"generated-media")


class CameraTests(unittest.TestCase):
    camera_a = "cam_abcdefgh12345678"
    camera_b = "cam_bbbbbbbb12345678"
    camera_c = "cam_cccccccc12345678"
    session = "sess_abcdefgh12345678"

    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.store = Store(self.temp)
        self.app = create_app({
            "BEHAVIOR_STORE": self.store,
            "BEHAVIOR_ANALYZER": FakeAnalyzer(),
            "BEHAVIOR_START_WORKER": False,
            "BEHAVIOR_CAMERA_GLOBAL_PENDING": 3,
            "BEHAVIOR_CAMERA_PER_CAMERA_PENDING": 1,
            "BEHAVIOR_CAMERA_RETRY_AFTER_S": 7,
        })
        self.client = self.app.test_client()

    def tearDown(self):
        shutil.rmtree(self.temp, ignore_errors=True)

    def chunk(self, camera_id, session_id=None, chunk_id="chunk_abcdefgh12345678"):
        data = {
            "capture_session_id": session_id or self.session,
            "chunk_id": chunk_id,
            "video": (io.BytesIO(b"webm bytes"), "chunk.webm"),
        }
        with patch("behavior.subprocess.run", side_effect=write_ffmpeg_output):
            return self.client.post(
                f"/api/cameras/{camera_id}/chunks", data=data, content_type="multipart/form-data"
            )

    def test_camera_isolation_and_manual_upload_remain_compatible(self):
        first = self.chunk(self.camera_a, chunk_id="chunk_aaaaaaa12345678")
        self.assertEqual(first.status_code, 202)
        self.assertEqual(first.json["video"]["source_kind"], "browser_camera")
        self.assertEqual(first.json["video"]["camera_id"], self.camera_a)
        self.assertEqual(first.json["video"]["capture_session_id"], self.session)

        capped = self.chunk(self.camera_a, "sess_bbbbbbbb12345678", "chunk_bbbbbbb12345678")
        self.assertEqual(capped.status_code, 429)
        self.assertEqual(capped.json["scope"], "camera")
        self.assertEqual(capped.headers["Retry-After"], "7")
        self.assertEqual(self.client.get(f"/api/cameras/{self.camera_a}").json["camera"]["last_error"], "This camera queue is at capacity")

        other = self.chunk(self.camera_b, chunk_id="chunk_ccccccc12345678")
        self.assertEqual(other.status_code, 202)
        with patch("behavior.subprocess.run", side_effect=write_ffmpeg_output):
            manual = self.client.post(
                "/api/videos", data={"video": (io.BytesIO(b"manual"), "manual.webm")}, content_type="multipart/form-data"
            )
        self.assertEqual(manual.status_code, 202)
        self.assertEqual(manual.json["video"]["source_kind"], "upload")
        self.assertIsNone(manual.json["video"]["camera_id"])

    def test_global_cap_prevents_new_camera_ids_from_bypassing_bound(self):
        self.app = create_app({
            "BEHAVIOR_STORE": self.store,
            "BEHAVIOR_ANALYZER": FakeAnalyzer(),
            "BEHAVIOR_START_WORKER": False,
            "BEHAVIOR_CAMERA_GLOBAL_PENDING": 2,
            "BEHAVIOR_CAMERA_PER_CAMERA_PENDING": 2,
        })
        self.client = self.app.test_client()
        self.assertEqual(self.chunk(self.camera_a, chunk_id="chunk_ddddddd12345678").status_code, 202)
        self.assertEqual(self.chunk(self.camera_b, chunk_id="chunk_eeeeeee12345678").status_code, 202)
        response = self.chunk(self.camera_c, chunk_id="chunk_fffffff12345678")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json["scope"], "global")
        self.assertEqual(
            self.store.one("SELECT count(*) FROM jobs WHERE status IN ('queued','processing')")[0], 2
        )

    def test_simultaneous_admission_reserves_one_slot_atomically(self):
        queue = CameraQueue(self.store, CameraQueueLimits(global_pending=1, per_camera_pending=1, retry_after_s=1))
        barrier = threading.Barrier(4)
        outcomes = []
        outcome_lock = threading.Lock()

        def admit(n):
            vid, job = f"v{n}", f"j{n}"
            video = (vid, "camera.webm", "now", None, None, 8.0, "queued", None, str(self.temp / vid), None, "empty", 0, "[]", None, None, 0)
            analysis = (job, vid, "analysis", "queued", 0, "queued", None, "now", "now", 0)
            barrier.wait()
            try:
                queue.admit(video=video, job=analysis, camera_id=self.camera_a, session_id=self.session, chunk_id=f"chunk_{n}abcdefghi", received_at="2026-01-01T00:00:00Z")
                outcome = "accepted"
            except CameraAdmissionFull:
                outcome = "full"
            with outcome_lock:
                outcomes.append(outcome)

        threads = [threading.Thread(target=admit, args=(n,)) for n in range(3)]
        for thread in threads: thread.start()
        barrier.wait()
        for thread in threads: thread.join()
        self.assertEqual(outcomes.count("accepted"), 1)
        self.assertEqual(outcomes.count("full"), 2)
        self.assertEqual(self.store.one("SELECT count(*) FROM jobs")[0], 1)

    def test_camera_chunk_runs_through_job_event_and_review_clip(self):
        response = self.chunk(self.camera_a, chunk_id="chunk_ggggggg12345678")
        self.assertEqual(response.status_code, 202)
        video_id, job_id = response.json["video"]["id"], response.json["job"]["id"]
        with patch("behavior.subprocess.run", side_effect=write_ffmpeg_output):
            self.app.config["BEHAVIOR_WORKER"].run_job(job_id)
        detail = self.client.get(f"/api/videos/{video_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json["video"]["source_kind"], "browser_camera")
        self.assertEqual(detail.json["job"]["status"], "done")
        self.assertEqual(len(detail.json["events"]), 1)
        clip = self.client.get(detail.json["events"][0]["clip_url"])
        self.assertEqual(clip.status_code, 200)
        clip.close()

    def test_camera_ids_and_sessions_are_validated(self):
        bad_id = self.chunk("../../not-a-camera")
        self.assertEqual(bad_id.status_code, 404)
        bad_session = self.chunk(self.camera_a, "not-a-session")
        self.assertEqual(bad_session.status_code, 400)

    def test_retry_after_lost_response_reuses_the_same_durable_job(self):
        key = "chunk_hhhhhhh12345678"
        first = self.chunk(self.camera_a, chunk_id=key)
        self.assertEqual(first.status_code, 202)
        retry = self.chunk(self.camera_a, chunk_id=key)
        self.assertEqual(retry.status_code, 202)
        self.assertTrue(retry.json["duplicate"])
        self.assertEqual(retry.json["video"]["id"], first.json["video"]["id"])
        self.assertEqual(retry.json["job"]["id"], first.json["job"]["id"])
        self.assertEqual(self.store.one("SELECT count(*) FROM jobs")[0], 1)

    def test_oversized_camera_chunk_leaves_no_durable_source(self):
        with patch("behavior.MAX_CAMERA_CHUNK_BYTES", 4), patch(
            "behavior.MAX_CAMERA_MULTIPART_OVERHEAD_BYTES", 0
        ):
            response = self.chunk(self.camera_a, chunk_id="chunk_iiiiiii12345678")
        self.assertEqual(response.status_code, 413)
        self.assertEqual(list(self.store.media.iterdir()), [])
        self.assertEqual(self.store.one("SELECT count(*) FROM videos")[0], 0)


if __name__ == "__main__":
    unittest.main()
