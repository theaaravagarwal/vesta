"""Fixed-camera boundary collector primitives.

The collector is intentionally a local sidecar process.  The review web
process only writes desired state and reads its durable heartbeat; RTSP
credentials and decoded frames never cross the HTTP boundary.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from .capture import validate_camera_id

# Must be set before OpenCV's FFmpeg backend is imported. It keeps native
# connection diagnostics out of service stderr, where an RTSP URL can embed a
# credential. Python-level errors are separately redacted below.
os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")
_NATIVE_STDERR_LOCK = threading.Lock()


VIEW_ID = re.compile(r"^view_[A-Za-z0-9]{16,64}$")
PREVIEW_ID = re.compile(r"^preview_[A-Za-z0-9]{16,64}$")
LINE_ID = re.compile(r"^line_[A-Za-z0-9_-]{8,64}$")
MAX_LINES = 24


def validate_id(value: object, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{label} is invalid")
    return value


def _point(value: object) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("line points must be normalized [x,y] pairs")
    result = []
    for item in value:
        if not isinstance(item, (int, float)) or isinstance(item, bool) or not math.isfinite(item):
            raise ValueError("line points must be finite numbers")
        if not 0 <= float(item) <= 1:
            raise ValueError("line points must be between 0 and 1")
        result.append(round(float(item), 6))
    return result


def validate_lines(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > MAX_LINES:
        raise ValueError(f"lines must contain at most {MAX_LINES} entries")
    seen: set[str] = set()
    output = []
    for line in value:
        if not isinstance(line, dict) or set(line) - {"id", "label", "start", "end", "direction", "enabled"}:
            raise ValueError("invalid line fields")
        label = line.get("label", "")
        if not isinstance(label, str) or not (1 <= len(label.strip()) <= 80):
            raise ValueError("line label must be 1 to 80 characters")
        start, end = _point(line.get("start")), _point(line.get("end"))
        if math.dist(start, end) < 0.02:
            raise ValueError("line endpoints must be separated")
        direction = line.get("direction")
        if direction not in {"a_to_b", "b_to_a", "either"}:
            raise ValueError("line direction is invalid")
        enabled = line.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("line enabled must be boolean")
        line_id = line.get("id") or f"line_{uuid.uuid4().hex}"
        line_id = validate_id(line_id, LINE_ID, "line id")
        if line_id in seen:
            raise ValueError("line ids must be unique")
        seen.add(line_id)
        output.append({"id": line_id, "label": label.strip(), "start": start, "end": end,
                       "direction": direction, "enabled": enabled})
    return output


def ground_point(observation: dict[str, Any]) -> tuple[float, float] | None:
    value = observation.get("ground_point")
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            x, y = float(value[0]), float(value[1])
            if math.isfinite(x) and math.isfinite(y) and 0 <= x <= 1 and 0 <= y <= 1:
                return x, y
        except (TypeError, ValueError):
            return None
    box = observation.get("normalized_box")
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    try:
        x1, y1, x2, y2 = [float(item) for item in box]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(item) for item in (x1, y1, x2, y2)):
        return None
    if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
        return None
    return (x1 + x2) / 2, y2


def _signed_distance(line: dict[str, Any], point: tuple[float, float]) -> tuple[float, float]:
    ax, ay = line["start"]
    bx, by = line["end"]
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    # Cross product / length is signed perpendicular distance. Projection
    # enforces a finite segment rather than treating the drawn line as infinite.
    signed = (dx * (point[1] - ay) - dy * (point[0] - ax)) / length
    projection = ((point[0] - ax) * dx + (point[1] - ay) * dy) / (length * length)
    return signed, projection


@dataclass(frozen=True)
class Crossing:
    line_id: str
    track_id: str
    direction: str
    started_at: float
    crossed_at: float
    point: tuple[float, float]


class CrossingEngine:
    """Per-camera crossing state with segment bounds, hysteresis, and dedupe."""

    def __init__(self, lines: list[dict[str, Any]], hysteresis: float = 0.015,
                 endpoint_margin: float = 0.06, dedupe_s: float = 3.0):
        self.lines = [line for line in lines if line["enabled"]]
        self.hysteresis = hysteresis
        self.endpoint_margin = endpoint_margin
        self.dedupe_s = dedupe_s
        self._sides: dict[tuple[str, str], int] = {}
        self._observed_at: dict[tuple[str, str], float] = {}
        self._dedupe: dict[tuple[str, str, str], float] = {}

    def reset(self) -> None:
        self._sides.clear()
        self._observed_at.clear()
        self._dedupe.clear()

    def update(self, observations: list[dict[str, Any]], timestamp: float) -> list[Crossing]:
        crosses: list[Crossing] = []
        for observation in observations:
            track_id = str(observation.get("track_id", ""))
            point = ground_point(observation)
            if not track_id or point is None:
                continue
            for line in self.lines:
                signed, projection = _signed_distance(line, point)
                if not -self.endpoint_margin <= projection <= 1 + self.endpoint_margin:
                    continue
                if abs(signed) < self.hysteresis:
                    continue
                side = 1 if signed > 0 else -1
                key = (line["id"], track_id)
                old = self._sides.get(key)
                self._sides[key] = side
                if old is None or old == side:
                    self._observed_at[key] = timestamp
                    continue
                direction = "a_to_b" if old < side else "b_to_a"
                if line["direction"] not in {"either", direction}:
                    continue
                # A per-direction cooldown rejects duplicate jitter in one
                # direction but preserves a genuine rapid return crossing.
                dedupe_key = (line["id"], track_id, direction)
                previous = self._dedupe.get(dedupe_key)
                if previous is not None and timestamp - previous < self.dedupe_s:
                    continue
                self._dedupe[dedupe_key] = timestamp
                started_at = self._observed_at.get(key, timestamp)
                self._observed_at[key] = timestamp
                crosses.append(Crossing(line["id"], track_id, direction, started_at, timestamp, point))
        return crosses


class CameraSecrets:
    """Read only a local, mode-0600 camera secret file; never return its URL."""

    def __init__(self, root: Path):
        self.root = Path(root).expanduser()

    def _path(self, camera_id: str) -> Path:
        validate_camera_id(camera_id)
        return self.root / f"{camera_id}.json"

    def configured(self, camera_id: str) -> bool:
        try:
            self.rtsp_url(camera_id)
            return True
        except ValueError:
            return False

    def rtsp_url(self, camera_id: str) -> str:
        path = self._path(camera_id)
        try:
            if path.stat().st_mode & 0o077:
                raise ValueError("camera secret permissions must be owner-only")
            parsed = json.loads(path.read_text(encoding="utf-8"))
            url = parsed.get("rtsp_url") if isinstance(parsed, dict) else None
        except FileNotFoundError as exc:
            raise ValueError("camera credentials are not configured locally") from exc
        except json.JSONDecodeError as exc:
            raise ValueError("camera credential file is invalid") from exc
        if not isinstance(url, str) or len(url) > 4096:
            raise ValueError("camera credential file is invalid")
        split = urlsplit(url)
        if split.scheme not in {"rtsp", "rtsps"} or not split.hostname or split.fragment:
            raise ValueError("camera credential file is invalid")
        return url


@dataclass
class _PendingCrossing:
    crossing: Crossing
    pre_frames: list[bytes]
    post_frames: list[bytes]
    sequence: int


class BoundaryCollectorService:
    """Single local collector process, coordinated through the shared SQLite store."""

    def __init__(self, store: Any, secrets: CameraSecrets, *, model_path: str | None = None,
                 tracker_factory: Callable[[str], Any] | None = None, max_cameras: int = 4,
                 pre_frames: int = 12, post_frames: int = 8):
        self.store = store
        self.secrets = secrets
        self.model_path = model_path or os.getenv("BEHAVIOR_BOUNDARY_YOLO_MODEL", "")
        self.tracker_factory = tracker_factory
        self.max_cameras = max_cameras
        self.pre_frames = pre_frames
        self.post_frames = post_frames
        self._stop = threading.Event()
        self._runners: dict[str, tuple[threading.Thread, threading.Event]] = {}
        self._lock = threading.Lock()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            runners = list(self._runners.values())
        for _, event in runners:
            event.set()
        for thread, _ in runners:
            thread.join(timeout=5)

    def run_forever(self, poll_s: float = 0.5) -> None:
        while not self._stop.wait(poll_s):
            self.run_once()

    def run_once(self) -> None:
        wanted = self.store.boundary_desired_cameras()
        active = set()
        for camera in wanted:
            camera_id, desired = camera["id"], camera["desired_state"]
            active.add(camera_id)
            if desired == "setup":
                if camera_id not in self._runners:
                    self._start(camera_id, self._setup_once)
            elif desired == "register":
                if camera_id not in self._runners:
                    self._start(camera_id, self._registration_once)
            elif desired == "running":
                if camera_id not in self._runners:
                    if len(self._runners) >= self.max_cameras:
                        self.store.boundary_status(camera_id, state="stopped", error="collector camera limit reached")
                    else:
                        self._start(camera_id, self._capture_loop)
            else:
                self._stop_camera(camera_id)
        with self._lock:
            stale = [camera_id for camera_id in self._runners if camera_id not in active]
        for camera_id in stale:
            self._stop_camera(camera_id)

    def _start(self, camera_id: str, target: Callable[[str, threading.Event], None]) -> None:
        cancelled = threading.Event()
        thread = threading.Thread(target=self._run_camera, args=(camera_id, cancelled, target), daemon=True,
                                  name=f"boundary-{camera_id[-8:]}")
        with self._lock:
            self._runners[camera_id] = (thread, cancelled)
        thread.start()

    def _run_camera(self, camera_id: str, cancelled: threading.Event,
                    target: Callable[[str, threading.Event], None]) -> None:
        try:
            target(camera_id, cancelled)
        except Exception as exc:
            # Error strings are deliberately generic: exceptions from OpenCV can
            # include an RTSP URL with embedded credentials.
            self.store.boundary_status(camera_id, state="error", error="collector failed")
        finally:
            with self._lock:
                self._runners.pop(camera_id, None)

    def _stop_camera(self, camera_id: str) -> None:
        with self._lock:
            runner = self._runners.get(camera_id)
        if runner:
            runner[1].set()

    @staticmethod
    def _cv2():
        import cv2
        # OpenCV/FFmpeg can otherwise write connection diagnostics containing an
        # RTSP URL (and embedded password) directly to service stderr.
        if hasattr(cv2, "setLogLevel"):
            cv2.setLogLevel(0)
        return cv2

    @staticmethod
    def _quiet_native(callable_):
        """Run OpenCV native work without allowing RTSP diagnostics onto stderr."""
        with _NATIVE_STDERR_LOCK:
            saved = os.dup(2)
            null = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(null, 2)
                return callable_()
            finally:
                os.dup2(saved, 2)
                os.close(saved)
                os.close(null)

    def _open(self, camera_id: str):
        cv2 = self._cv2()
        capture = self._quiet_native(lambda: cv2.VideoCapture(self.secrets.rtsp_url(camera_id)))
        if not capture.isOpened():
            self._quiet_native(capture.release)
            raise RuntimeError("stream unavailable")
        return capture

    def _setup_once(self, camera_id: str, cancelled: threading.Event) -> None:
        self.store.boundary_status(camera_id, state="setup", error=None)
        try:
            capture = self._open(camera_id)
        except Exception:
            self.store.boundary_status(camera_id, state="error", error="reference capture could not open stream")
            return
        try:
            deadline = time.monotonic() + 12
            while not cancelled.is_set() and time.monotonic() < deadline:
                ok, frame = self._quiet_native(capture.read)
                if ok:
                    encoded_ok, encoded = self._cv2().imencode(".jpg", frame)
                    if encoded_ok:
                        self.store.save_boundary_preview(camera_id, bytes(encoded))
                        self.store.boundary_status(camera_id, state="stopped", error=None,
                                                   reason="reference frame captured")
                        self.store.boundary_set_desired(camera_id, "stopped")
                        return
                cancelled.wait(0.1)
            self.store.boundary_status(camera_id, state="error", error="reference capture timed out")
        finally:
            self._quiet_native(capture.release)

    def _registration_once(self, camera_id: str, cancelled: threading.Event) -> None:
        """Perform a single proposal off the request thread, then remain idle."""
        preview_id = self.store.boundary_registration_preview(camera_id)
        if not preview_id or cancelled.is_set():
            return
        self.store.boundary_status(camera_id, state="paused", error=None, alerts_paused=True,
                                   reason="reference registration is being reviewed")
        try:
            result = self.store.boundary_complete_registration(camera_id, preview_id)
        except Exception as exc:
            # Registration failure is calibration uncertainty, never permission
            # to fall back to a previously active image-plane mapping.
            message = str(exc)
            self.store.boundary_fail_registration(camera_id, message)
            self.store.boundary_status(camera_id, state="paused", error=None, alerts_paused=True,
                                       reason=message)
            return
        self.store.boundary_set_desired(camera_id, "stopped")
        self.store.boundary_status(camera_id, state="paused", error=None, alerts_paused=True,
                                   reason="registration proposed; manual approval is required")

    def _tracker(self):
        if not self.model_path or not Path(self.model_path).is_file():
            raise RuntimeError("person tracker model is unavailable locally")
        if self.tracker_factory:
            return self.tracker_factory(self.model_path)
        from ultralytics import YOLO
        return YOLO(self.model_path)

    @staticmethod
    def _observations(result: Any, width: int, height: int) -> list[dict[str, Any]]:
        boxes = getattr(result, "boxes", None)
        if boxes is None or getattr(boxes, "id", None) is None:
            return []
        xyxy = boxes.xyxy.cpu().tolist()
        ids = boxes.id.int().cpu().tolist()
        classes = boxes.cls.int().cpu().tolist() if getattr(boxes, "cls", None) is not None else []
        observations = []
        for index, box in enumerate(xyxy):
            if index >= len(ids) or classes and classes[index] != 0 or len(box) != 4:
                continue
            x1, y1, x2, y2 = box
            normalized = [max(0.0, min(1.0, x1 / width)), max(0.0, min(1.0, y1 / height)),
                          max(0.0, min(1.0, x2 / width)), max(0.0, min(1.0, y2 / height))]
            if normalized[2] > normalized[0] and normalized[3] > normalized[1]:
                observations.append({"track_id": str(ids[index]), "normalized_box": normalized})
        return observations

    def _capture_loop(self, camera_id: str, cancelled: threading.Event) -> None:
        config = self.store.boundary_camera(camera_id)
        if not config or config["calibration_state"] != "calibrated" or not config["active_revision_id"]:
            self.store.boundary_status(camera_id, state="stopped", error=None, alerts_paused=True,
                                       reason="an approved reference view with enabled lines is required")
            return
        lines = config["lines"]
        if not any(line["enabled"] for line in lines):
            self.store.boundary_status(camera_id, state="stopped", error=None, alerts_paused=True,
                                       reason="an approved reference view with enabled lines is required")
            return
        try:
            tracker = self._tracker()
        except Exception:
            self.store.boundary_status(camera_id, state="error", error="person tracker model is unavailable locally")
            return
        session_id = config.get("desired_test_session_id") or f"bsess_{uuid.uuid4().hex}"
        test_session = session_id.startswith("tsess_")
        epoch, sequence = 0, 0
        self.store.boundary_session_start(session_id, camera_id, epoch, config["active_revision_id"])
        self.store.boundary_status(camera_id, state="connecting", session_id=session_id, epoch=epoch,
                                   sequence=sequence, error=None, alerts_paused=False, reason=None)
        engine = CrossingEngine(lines)
        pre: deque[bytes] = deque(maxlen=self.pre_frames)
        pending: list[_PendingCrossing] = []
        test_frames: list[bytes] = []
        test_segment_started = time.monotonic()
        test_last_sample = 0.0
        session_started = test_segment_started
        capture = None
        last_frame_at = 0.0
        last_alignment_check = 0.0
        unverifiable_alignment_checks = 0
        alignment_uncertain = False
        capture_gap_started: float | None = None
        alignment_gap_started: float | None = None

        def record_gap(ended: float, reason: str) -> None:
            nonlocal capture_gap_started
            if test_session and capture_gap_started is not None:
                self.store.boundary_add_gap(session_id, capture_gap_started - session_started,
                                            ended - session_started, reason)
            capture_gap_started = None

        def pause_for_alignment(reason: str, image: bytes) -> None:
            nonlocal alignment_gap_started
            if test_session and alignment_gap_started is not None:
                self.store.boundary_add_gap(session_id, alignment_gap_started - session_started,
                                            time.monotonic() - session_started, "calibration_unverified")
                alignment_gap_started = None
            self.store.save_boundary_preview(camera_id, image)
            self.store.boundary_mark_calibration_uncertain(camera_id, reason)
            engine.reset()
            pre.clear()
            pending.clear()
        try:
            while not cancelled.is_set() and not self._stop.is_set():
                current = self.store.boundary_camera(camera_id)
                if (not current or current["desired_state"] != "running" or
                        (test_session and current.get("desired_test_session_id") != session_id)):
                    break
                if current["calibration_state"] != "calibrated" or current["active_revision_id"] != config["active_revision_id"]:
                    self.store.boundary_status(camera_id, state="paused", session_id=session_id, epoch=epoch,
                                               sequence=sequence, alerts_paused=True,
                                               reason="reference view changed; approval and a new start are required")
                    break
                if capture is None:
                    try:
                        capture = self._open(camera_id)
                        epoch += 1
                        engine.reset()
                        pre.clear()
                        record_gap(time.monotonic(), "capture_reconnected")
                        self.store.boundary_status(camera_id, state="running", session_id=session_id, epoch=epoch,
                                                   sequence=sequence, error=None, alerts_paused=False, reason=None)
                    except Exception:
                        self.store.boundary_status(camera_id, state="reconnecting", session_id=session_id, epoch=epoch,
                                                   sequence=sequence, error="stream unavailable")
                        cancelled.wait(1.0)
                        continue
                ok, frame = self._quiet_native(capture.read)
                now = time.monotonic()
                if not ok:
                    if capture_gap_started is None:
                        capture_gap_started = now
                    self._quiet_native(capture.release)
                    capture = None
                    engine.reset()
                    pre.clear()
                    continue
                if last_frame_at and now - last_frame_at > 2.5:
                    engine.reset()
                    pre.clear()
                    if capture_gap_started is None:
                        capture_gap_started = last_frame_at
                    record_gap(now, "capture_gap")
                last_frame_at = now
                sequence += 1
                encoded_ok, encoded = self._cv2().imencode(".jpg", frame)
                image = bytes(encoded) if encoded_ok else b""
                if image:
                    # Fixed-view geometry is only valid while the reference
                    # still aligns. This bounded periodic check is deliberately
                    # conservative: a detected move pauses immediately, and
                    # three failed comparisons pause instead of guessing.
                    if now - last_alignment_check >= 5.0:
                        last_alignment_check = now
                        aligned, alignment_reason = self.store.boundary_reference_alignment(camera_id, image)
                        if aligned is True:
                            if alignment_uncertain:
                                # Do not connect track state across an interval
                                # where the fixed view could not be verified.
                                engine.reset()
                                pre.clear()
                            unverifiable_alignment_checks = 0
                            alignment_uncertain = False
                            if test_session and alignment_gap_started is not None:
                                self.store.boundary_add_gap(session_id, alignment_gap_started - session_started,
                                                            now - session_started, "calibration_unverified")
                                alignment_gap_started = None
                        else:
                            unverifiable_alignment_checks += 1
                            alignment_uncertain = True
                            if alignment_gap_started is None:
                                alignment_gap_started = now
                            if aligned is False or unverifiable_alignment_checks >= 3:
                                reason = alignment_reason if aligned is False else "reference alignment remained unverifiable"
                                pause_for_alignment(reason, image)
                                break
                    pre.append(image)
                    if test_session and now - test_last_sample >= 0.2:
                        test_frames.append(image)
                        test_last_sample = now
                    if test_session and now - test_segment_started >= 30 and test_frames:
                        self.store.create_boundary_test_segment(session_id, camera_id,
                                                                test_segment_started - session_started,
                                                                now - session_started, test_frames)
                        test_frames = []
                        test_segment_started = now
                if alignment_uncertain:
                    # Pixels may still be retained for a supervised test, but
                    # no boundary semantic is emitted until alignment verifies.
                    self.store.boundary_status(camera_id, state="running", session_id=session_id, epoch=epoch,
                                               sequence=sequence, last_frame_at=time.time(), error=None,
                                               alerts_paused=True, reason="reference alignment is temporarily unverifiable")
                    continue
                result = tracker.track(frame, persist=True, classes=[0], verbose=False)[0]
                observations = self._observations(result, frame.shape[1], frame.shape[0])
                crossings = engine.update(observations, now)
                ready = any(len(item.post_frames) + (1 if image else 0) >= self.post_frames for item in pending)
                if crossings or ready:
                    # A movement between periodic health checks cannot turn
                    # into an event: verify the actual candidate frame first.
                    aligned, alignment_reason = self.store.boundary_reference_alignment(camera_id, image)
                    if aligned is not True:
                        pause_for_alignment(alignment_reason if aligned is False else "reference alignment is unverifiable", image)
                        break
                for item in pending[:]:
                    if image:
                        item.post_frames.append(image)
                    if len(item.post_frames) >= self.post_frames:
                        self.store.create_boundary_event(camera_id=camera_id, session_id=session_id, epoch=epoch,
                                                         sequence=item.sequence, revision_id=config["active_revision_id"],
                                                         crossing=item.crossing, pre_frames=item.pre_frames,
                                                         post_frames=item.post_frames, tracker_model=Path(self.model_path).name)
                        pending.remove(item)
                for crossing in crossings:
                    ended_at = time.time()
                    persisted_crossing = Crossing(crossing.line_id, crossing.track_id, crossing.direction,
                                                  ended_at - (crossing.crossed_at - crossing.started_at),
                                                  ended_at, crossing.point)
                    pending.append(_PendingCrossing(persisted_crossing, list(pre), [], sequence))
                self.store.boundary_status(camera_id, state="running", session_id=session_id, epoch=epoch,
                                           sequence=sequence, last_frame_at=time.time(), error=None,
                                           alerts_paused=False, reason=None)
        finally:
            if capture is not None:
                self._quiet_native(capture.release)
            if test_session and test_frames:
                try:
                    self.store.create_boundary_test_segment(session_id, camera_id,
                                                            test_segment_started - session_started,
                                                            time.monotonic() - session_started, test_frames)
                except Exception:
                    self.store.boundary_status(camera_id, state="error", error="test evidence segment could not be saved")
            self.store.boundary_session_end(session_id, "stopped")
            status = self.store.boundary_camera(camera_id)
            if status and status["desired_state"] == "stopped" and status["calibration_state"] == "calibrated":
                self.store.boundary_status(camera_id, state="stopped", session_id=session_id, epoch=epoch,
                                           sequence=sequence, error=None)
