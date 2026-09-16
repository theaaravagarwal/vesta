"""Durable, upload-only temporal review service.

The module deliberately has no import-time model loading or camera access.  A
single :class:`BehaviorWorker` owns queued work and (when enabled) model use.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from flask import Blueprint, Flask, jsonify, redirect, request, send_file
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge
from werkzeug.utils import secure_filename

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
WINDOW_S, STRIDE_S, SAMPLE_FPS = 8.0, 4.0, 2.0
EVENT_POLICY = os.getenv("BEHAVIOR_EVENT_POLICY", "baseline")
if EVENT_POLICY not in {"baseline", "observable-v3"}:
    raise ValueError("BEHAVIOR_EVENT_POLICY must be baseline or observable-v3")
# Spatial crops and the event prompt are independent variables.  Scoring one
# against the other requires selecting them separately, so a focus run records
# its own config version rather than implying a different event policy.
FOCUS_VIEW = os.getenv("BEHAVIOR_FOCUS_VIEW", "0").strip() == "1"
# One continuous action is reported once per overlapping window, so the same
# action resuming within a short gap is one candidate rather than several.  Zero
# keeps the previous overlap-only behavior.
MERGE_GAP_S = float(os.getenv("BEHAVIOR_MERGE_GAP_S", "0"))
if not 0 <= MERGE_GAP_S <= WINDOW_S:
    raise ValueError(f"BEHAVIOR_MERGE_GAP_S must be between 0 and {WINDOW_S}")


def config_version() -> str:
    """Provenance tag written onto every event produced by this process."""
    policy = "temporal-v3-actions" if EVENT_POLICY == "observable-v3" else "temporal-v3-bounded"
    gap = f"-gap{MERGE_GAP_S:g}" if MERGE_GAP_S else ""
    return policy + ("-focus" if FOCUS_VIEW else "") + gap + "-evidence2"


CONFIG_VERSION = config_version()


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def json_error(message: str, status: int = 400):
    return jsonify({"error": message}), status


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


class Store:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        root = self.root
        self.media = root / "media"
        self.frames = root / "frames"
        self.clips = root / "clips"
        for p in (self.root, self.media, self.frames, self.clips):
            p.mkdir(parents=True, exist_ok=True)
        self.db_path = root / "behavior.sqlite3"
        self.lock = threading.RLock()
        self._init()

    @staticmethod
    def _absolute_path(value: str | None) -> str | None:
        """Migrate paths saved before runtime paths were made absolute."""
        if not value:
            return value
        path = Path(value)
        return str(path if path.is_absolute() else (Path.cwd() / path).resolve())

    def conn(self):
        c = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    def _init(self):
        c = self.conn()
        try:
            c.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS videos (id TEXT PRIMARY KEY,name TEXT,created_at TEXT,captured_at TEXT,timezone TEXT,duration_s REAL,status TEXT,error TEXT,path TEXT,frame_path TEXT,scene_status TEXT DEFAULT 'empty',scene_approved INTEGER DEFAULT 0,regions TEXT DEFAULT '[]',schedule TEXT,scene_error TEXT,scene_version INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY,video_id TEXT,type TEXT,status TEXT,progress INTEGER,stage TEXT,error TEXT,created_at TEXT,updated_at TEXT,cancelled INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY,video_id TEXT,start_s REAL,end_s REAL,action TEXT,description TEXT,evidence TEXT,uncertainty TEXT,track_ids TEXT,review_status TEXT DEFAULT 'unreviewed',pinned INTEGER DEFAULT 0,correction TEXT DEFAULT '',clip_path TEXT,model TEXT,config_version TEXT);
            CREATE TABLE IF NOT EXISTS analysis_windows (job_id TEXT,video_id TEXT,window_index INTEGER,start_s REAL,end_s REAL,frame_count INTEGER,status TEXT,candidate_count INTEGER DEFAULT 0,model TEXT,config_version TEXT,PRIMARY KEY(job_id,window_index));
            CREATE TABLE IF NOT EXISTS candidate_traces (id TEXT PRIMARY KEY,job_id TEXT,video_id TEXT,window_index INTEGER,start_s REAL,end_s REAL,action TEXT,description TEXT,evidence TEXT,uncertainty TEXT,decision TEXT,reason TEXT,model TEXT,config_version TEXT,created_at TEXT);
            CREATE INDEX IF NOT EXISTS candidate_traces_video_job ON candidate_traces(video_id,job_id,window_index);
            CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY,event_id TEXT UNIQUE,created_at TEXT,status TEXT,action TEXT);
            """)
            columns = {
                row[1] for row in c.execute("PRAGMA table_info(analysis_windows)")
            }
            for column in ("model", "config_version"):
                if column not in columns:
                    c.execute(f"ALTER TABLE analysis_windows ADD COLUMN {column} TEXT")
            # An interrupted process cannot leave a job permanently processing.
            c.execute(
                "UPDATE jobs SET status='queued', stage='recovered', updated_at=? WHERE status='processing' AND cancelled=0",
                (utcnow(),),
            )
            c.execute(
                "UPDATE jobs SET status='cancelled', stage='cancelled', updated_at=? WHERE cancelled=1 AND status IN ('queued','processing')",
                (utcnow(),),
            )
            for table, column in (("videos", "path"), ("videos", "frame_path"), ("events", "clip_path")):
                rows = c.execute(f"SELECT rowid, {column} FROM {table} WHERE {column} IS NOT NULL").fetchall()
                for row in rows:
                    absolute = self._absolute_path(row[column])
                    if absolute != row[column]:
                        c.execute(f"UPDATE {table} SET {column}=? WHERE rowid=?", (absolute, row["rowid"]))
        finally:
            c.commit()
            c.close()

    def one(self, q, args=()):
        with self.lock:
            c = self.conn()
            try:
                return c.execute(q, args).fetchone()
            finally:
                c.close()

    def all(self, q, args=()):
        with self.lock:
            c = self.conn()
            try:
                return c.execute(q, args).fetchall()
            finally:
                c.close()

    def run(self, q, args=()):
        with self.lock:
            c = self.conn()
            try:
                c.execute(q, args)
                c.commit()
            finally:
                c.close()

    def job(self, row):
        return {k: row[k] for k in ("id", "status", "progress", "stage", "error")}

    def video(self, r):
        media_path = Path(r["path"])
        return {
            "id": r["id"],
            "name": r["name"],
            "created_at": r["created_at"],
            "captured_at": r["captured_at"],
            "timezone": r["timezone"],
            "duration_s": r["duration_s"],
            "status": r["status"],
            "media_url": f"/api/videos/{r['id']}/media",
            "frame_url": f"/api/videos/{r['id']}/frame",
            "media_ready": r["status"] != "evicted"
            and media_path.name == f"{r['id']}.mp4"
            and media_path.is_file(),
            "error": r["error"],
        }

    def scene(self, r):
        return {
            "status": r["scene_status"],
            "approved": bool(r["scene_approved"]),
            "regions": json.loads(r["regions"] or "[]"),
            "schedule": json.loads(r["schedule"]) if r["schedule"] else None,
            "error": r["scene_error"],
        }

    def system(self):
        usage = shutil.disk_usage(self.root)
        return {
            "used_percent": round((usage.total - usage.free) * 100 / usage.total, 1),
            "free_gb": round(usage.free / 1024**3, 2),
        }

    def cleanup(self):
        with self.lock:
            s = self.system()
            if s["used_percent"] < 85 and s["free_gb"] >= 50:
                return {**s, "paused": False, "message": ""}
            active_ids = {
                r[0]
                for r in self.all(
                    "SELECT DISTINCT video_id FROM jobs WHERE status IN ('queued','processing')"
                )
            }
            # Evict derived cache first, then oldest videos which have no protected evidence.
            for directory in (self.frames,):
                for p in sorted(directory.glob("*"), key=lambda x: x.stat().st_mtime):
                    # ``<video-id>.jpg`` is the only scene-annotation source.  Window
                    # folders and evidence clips are regenerable caches; this is not.
                    if (
                        directory == self.frames
                        and p.is_file()
                        and p.suffix.lower() == ".jpg"
                    ):
                        continue
                    if any(p.name.startswith(f"{vid}-") for vid in active_ids):
                        continue
                    if p.is_dir():
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        p.unlink(missing_ok=True)
                    s = self.system()
                    if s["used_percent"] <= 75 and s["free_gb"] >= 75:
                        return {**s, "paused": False, "message": ""}
            for r in self.all(
                "SELECT v.id,v.path,v.frame_path FROM videos v WHERE v.status NOT IN ('evicted','queued','processing') AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.video_id=v.id AND j.status IN ('queued','processing')) AND NOT EXISTS (SELECT 1 FROM events e WHERE e.video_id=v.id AND (e.pinned=1 OR e.review_status='confirmed' OR e.correction<>'')) ORDER BY v.created_at"
            ):
                Path(r["path"]).unlink(missing_ok=True)
                if r["frame_path"]:
                    Path(r["frame_path"]).unlink(missing_ok=True)
                for clip in self.all(
                    "SELECT clip_path FROM events WHERE video_id=?", (r["id"],)
                ):
                    if clip[0]:
                        Path(clip[0]).unlink(missing_ok=True)
                shutil.rmtree(self.frames / f"{r['id']}-tracks", ignore_errors=True)
                self.run(
                    "DELETE FROM outbox WHERE event_id IN (SELECT id FROM events WHERE video_id=?)",
                    (r["id"],),
                )
                self.run("DELETE FROM events WHERE video_id=?", (r["id"],))
                self.run("DELETE FROM candidate_traces WHERE video_id=?", (r["id"],))
                self.run("DELETE FROM analysis_windows WHERE video_id=?", (r["id"],))
                self.run(
                    "UPDATE videos SET status='evicted',error='Storage cleanup' WHERE id=?",
                    (r["id"],),
                )
                s = self.system()
                if s["used_percent"] <= 75 and s["free_gb"] >= 75:
                    return {**s, "paused": False, "message": ""}
            s = self.system()
            return {
                **s,
                "paused": True,
                "message": "Storage is full; protected evidence could not be reclaimed.",
            }


class TemporalAnalyzer:
    """Production analyzer. It samples every window before optional tracking."""

    model_name = os.getenv("BEHAVIOR_VLM_MODEL", "qwen2.5vl:3b")
    base_url = os.getenv(
        "BEHAVIOR_VLM_BASE_URL", os.getenv("LLAMACPP_BASE_URL", "http://127.0.0.1:8078")
    ).rstrip("/")

    def _request_events(self, payload: dict, token_budget: int, attempt: int) -> tuple[dict, str, str]:
        """Issue one structured request and retain only safe diagnostic metadata."""
        import urllib.request

        request_payload = {**payload, "max_tokens": token_budget}
        req = urllib.request.Request(
            self.base_url + "/v1/chat/completions",
            data=_json(request_payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as response:
                data = json.loads(response.read())
        except Exception as exc:
            raise RuntimeError(
                f"event model request failed (attempt={attempt}, model={self.model_name}, finish_reason=unavailable): {exc}"
            ) from exc
        model = str(data.get("model") or self.model_name) if isinstance(data, dict) else self.model_name
        try:
            choice = data["choices"][0]
            finish_reason = str(choice.get("finish_reason") or "unknown")
            content = choice["message"]["content"]
        except Exception as exc:
            raise RuntimeError(
                f"event model response failed (attempt={attempt}, model={model}, finish_reason=unknown): malformed response"
            ) from exc
        if finish_reason == "length":
            return {}, finish_reason, model
        try:
            parsed = json.loads(content) if isinstance(content, str) else content
        except Exception as exc:
            raise RuntimeError(
                f"event model response failed (attempt={attempt}, model={model}, finish_reason={finish_reason}): malformed JSON"
            ) from exc
        return parsed, finish_reason, model

    @staticmethod
    def _validate_event_output(parsed: object) -> list[dict]:
        if not isinstance(parsed, dict) or not isinstance(parsed.get("events"), list):
            raise ValueError("events must be an array")
        events = parsed["events"]
        if len(events) > 4:
            raise ValueError("too many events")
        allowed_actions = {"climbing", "boundary_entry", "access_interaction", "object_tampering", "other_observable_event"}
        for event in events:
            if not isinstance(event, dict) or set(event) != {"start_s", "end_s", "action", "description", "evidence", "uncertainty"}:
                raise ValueError("invalid event fields")
            if event["action"] not in allowed_actions or not isinstance(event["description"], str) or not 1 <= len(event["description"]) <= 240:
                raise ValueError("invalid event action or description")
            if not isinstance(event["uncertainty"], str) or len(event["uncertainty"]) > 240:
                raise ValueError("invalid uncertainty")
            evidence = event["evidence"]
            if not isinstance(evidence, list) or not 1 <= len(evidence) <= 3 or any(not isinstance(item, str) or not 1 <= len(item) <= 160 for item in evidence):
                raise ValueError("invalid evidence")
        return events

    def metadata(self, path: Path) -> float:
        p = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return float(p.stdout.strip())

    def frames(self, path: Path, out: Path, start: float, end: float) -> list[Path]:
        out.mkdir(parents=True, exist_ok=True)
        # timestamps are encoded in filenames and generated chronologically.
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(start),
                "-to",
                str(end),
                "-i",
                str(path),
                "-vf",
                f"fps={SAMPLE_FPS},scale='min(768,iw)':-2",
                str(out / "%06d.jpg"),
            ],
            capture_output=True,
            check=True,
        )
        return sorted(out.glob("*.jpg"))

    def track_video(self, path: Path, out: Path, duration: float) -> list[dict]:
        """Decode at 5 fps on CPU, then retain ByteTrack state for this video."""
        import cv2
        from ultralytics import YOLO

        model_path = Path(
            os.getenv(
                "BEHAVIOR_YOLO_MODEL",
                str(
                    Path(__file__).resolve().parent.parent
                    / "person-detect"
                    / "yolo26s.pt"
                ),
            )
        )
        if not model_path.is_file():
            raise RuntimeError(f"configured person model is unavailable: {model_path}")
        require_gpu = os.getenv("REQUIRE_GPU", "1").strip().lower() not in {
            "0",
            "false",
            "no",
        }
        if require_gpu:
            import torch

            if not torch.cuda.is_available():
                raise RuntimeError(
                    "GPU is required for person tracking but CUDA is unavailable"
                )
        device = os.getenv("BEHAVIOR_YOLO_DEVICE", "cuda:0" if require_gpu else "cpu")
        model = YOLO(model_path)  # one worker owns both model and a per-video tracker.
        cap = cv2.VideoCapture(str(path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        step = max(1, round(fps / 5))
        index = 0
        out.mkdir(parents=True, exist_ok=True)
        samples = []
        try:
            while True:
                ok, image = cap.read()
                if not ok:
                    break
                if index % step == 0:
                    frame = out / f"track-{index:08d}.jpg"
                    if not cv2.imwrite(str(frame), image):
                        raise RuntimeError("could not prepare tracking frame")
                    samples.append((index / fps, frame))
                index += 1
        finally:
            cap.release()
        if not samples:
            raise RuntimeError("video decode produced no tracking frames")
        observations = []
        for stamp, frame in samples:
            rs = model.track(
                str(frame),
                persist=True,
                tracker="bytetrack.yaml",
                classes=[0],
                verbose=False,
                device=device,
            )
            if not rs or rs[0].boxes.id is None:
                continue
            ids = rs[0].boxes.id.tolist()
            boxes = rs[0].boxes.xyxy.tolist()
            observations.extend(
                {
                    "time_s": stamp,
                    "track_id": str(int(i)),
                    "box": [round(float(v), 1) for v in b],
                    "normalized_box": [round(float(v) / (rs[0].orig_shape[1] if k % 2 == 0 else rs[0].orig_shape[0]), 6) for k, v in enumerate(b)],
                }
                for i, b in zip(ids, boxes)
            )
        return observations

    def infer(
        self,
        frames: list[Path],
        start: float,
        end: float,
        scene: dict,
        tracks: list[dict] | None = None,
        capture_context: dict | None = None,
    ) -> list[dict]:
        import urllib.request

        timestamps = [round(start + i / SAMPLE_FPS, 3) for i in range(len(frames))]
        images = []
        for stamp, f in zip(timestamps, frames):
            images.append({"type": "text", "text": f"Frame timestamp: {stamp:.3f}s"})
            images.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/jpeg;base64,"
                        + base64.b64encode(f.read_bytes()).decode()
                    },
                }
            )
        focus_note = (
            "Each image has two panels of the SAME moment: full scene LEFT, enlarged detail RIGHT. These are not two people or two cameras. "
            if frames and frames[0].stem.endswith("-focus") else ""
        )
        if EVENT_POLICY == "observable-v3":
            prompt = (
                "Review the chronological frames for observable physical actions, using the supplied timestamps. "
                + focus_note
                + "Compare changes across frames. Return at most four concise events, one per continuous action. "
                "Action definitions: climbing = lifting the body onto or over a wall/fence/obstacle or climbing through a window; ordinary stairs are not climbing. "
                "boundary_entry = visibly passing through a window or over/through an unusual barrier; routine door entry is not a candidate. "
                "access_interaction = repeated forceful pulls, pushes or attempts at a closed access point; simply opening/closing a door is not enough. "
                "object_tampering = visible striking, prying, cutting or damaging an object; merely touching or standing beside it is not enough. "
                "other_observable_event = another concrete non-routine physical action such as a fall or physical conflict, described specifically. "
                "Standing, walking, talking, gathering, ordinary cart pushing, carrying items and routine vehicle interaction must not create events by themselves. "
                "For each event give first and last supporting timestamps, the specific physical action, and one to three distinct observations with timestamps. "
                "Do not infer identity, intent, guilt, authorization or traits from appearance. Uncertainty about intent does not erase a visible action: describe the action and state what cannot be determined. "
                "If no qualifying physical action is visible, return exactly {\"events\":[]}. Never emit a placeholder, generic person-presence alert or speculation without visible evidence. "
                f"Frame timestamps: {timestamps}. Window: {start:.2f}-{end:.2f}s. "
                f"Approved scene context (advisory, not proof): {_json(scene)}. "
                f"Capture context: {_json(capture_context or {})}. Mention a schedule only when explicitly supplied."
            )
        else:
            prompt = (
                "Review this chronological 8-second video window. "
                + focus_note
                + "Frames are ordered and correspond to these exact video timestamps: "
                f"{timestamps}. Create events only for concrete non-routine candidate behaviors worth human review: climbing, unusual boundary entry, repeated access interaction, possible object tampering, or another specifically observable non-routine action. "
                    "Routine walking, gathering, or carrying tools alone must produce an empty events list. Use action only from climbing, boundary_entry, access_interaction, object_tampering, other_observable_event. "
                    "Do not label criminality. Keep an observable candidate event when its interpretation is uncertain and explain that uncertainty. Mention after-hours only when capture context supplies an approved schedule. "
                    "For no qualifying observation, return exactly {\"events\":[]}; never create a placeholder event. "
                "Do not infer identity, intent, guilt, danger, or clothing-based traits. Geometry is advisory, not proof. "
                "Return JSON {events:[{start_s,end_s,action,description,evidence,uncertainty}]}; use an empty array when uncertain. "
                f"Window is {start:.2f}-{end:.2f}s; scene context: {_json(scene)}; "
                f"optional person-track observations (not evidence of involvement): {_json((tracks or [])[:80])}; capture context: {_json(capture_context or {})}"
            )
        event_schema = {
            "type": "object", "required": ["events"], "additionalProperties": False,
            "properties": {"events": {"type": "array", "items": {
                "type": "object", "required": ["start_s", "end_s", "action", "description", "evidence", "uncertainty"], "additionalProperties": False,
                "properties": {"start_s": {"type": "number"}, "end_s": {"type": "number"}, "action": {"type": "string", "enum": ["climbing", "boundary_entry", "access_interaction", "object_tampering", "other_observable_event"]}, "description": {"type": "string", "minLength": 1}, "evidence": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}}, "uncertainty": {"type": "string"}}
            }}}
        }
        event_item = event_schema["properties"]["events"]["items"]
        event_schema["properties"]["events"]["maxItems"] = 4
        event_item["properties"]["description"]["maxLength"] = 240
        event_item["properties"]["evidence"]["maxItems"] = 3
        event_item["properties"]["evidence"]["items"]["maxLength"] = 160
        event_item["properties"]["uncertainty"]["maxLength"] = 240
        payload = (
            {
                "model": self.model_name,
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": prompt}, *images],
                    }
                ],
                "temperature": 0.0 if EVENT_POLICY == "observable-v3" else 0.1,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "behavior_events",
                        "strict": True,
                        "schema": event_schema,
                    },
                },
            }
        )
        initial = min(4096, max(1, int(os.getenv("BEHAVIOR_EVENT_MAX_TOKENS", "1536"))))
        retry = min(4096, max(initial, int(os.getenv("BEHAVIOR_EVENT_RETRY_MAX_TOKENS", "3072"))))
        for attempt, budget in enumerate((initial, retry), 1):
            parsed, finish_reason, model = self._request_events(payload, budget, attempt)
            if finish_reason == "length":
                if attempt == 2:
                    raise RuntimeError(f"event model response failed (attempt=2, model={model}, finish_reason=length): output truncated after retry")
                import logging
                logging.getLogger(__name__).warning("Retrying truncated event response: model=%s attempt=%s budget=%s", model, attempt, budget)
                continue
            try:
                return self._validate_event_output(parsed)
            except ValueError as exc:
                raise RuntimeError(f"event model response failed (attempt={attempt}, model={model}, finish_reason={finish_reason}): {exc}") from exc
        raise RuntimeError(f"event model response failed (attempt=2, model={self.model_name}, finish_reason=length): output truncated after retry")

    def suggest_scene(self, frame: Path) -> list[dict]:
        """Ask the same local VLM for editable, explicitly uncertain proposals."""
        import urllib.request

        if not frame.is_file():
            raise RuntimeError("Representative frame is unavailable")
        image = (
            "data:image/jpeg;base64," + base64.b64encode(frame.read_bytes()).decode()
        )
        text = (
            "Suggest visible scene regions from this frame only. Do not claim boundaries are exact. "
            "Return JSON {regions:[{id,label,kind,points:[[x,y],...]}]}; x/y must be normalized 0..1, "
            "kind must be fence, entrance, restricted, or other, each polygon has at least 3 points. "
            "For no reliable region return exactly {\"regions\":[]}. An illustrative valid region is "
            "{\"id\":\"entrance-1\",\"label\":\"visible entrance\",\"kind\":\"entrance\",\"points\":[[0.1,0.2],[0.3,0.2],[0.3,0.5]]}. "
            "These are unapproved editable proposals."
        )
        scene_schema = {
            "type": "object",
            "required": ["regions"],
            "additionalProperties": False,
            "properties": {
                "regions": {
                    "type": "array",
                    "maxItems": 20,
                    "items": {
                        "type": "object",
                        "required": ["id", "label", "kind", "points"],
                        "additionalProperties": False,
                        "properties": {
                            "id": {"type": "string", "minLength": 1, "maxLength": 80},
                            "label": {"type": "string", "minLength": 1, "maxLength": 120},
                            "kind": {"type": "string", "enum": ["fence", "entrance", "restricted", "other"]},
                            "points": {"type": "array", "minItems": 3, "maxItems": 32, "items": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "number", "minimum": 0, "maximum": 1}}},
                        },
                    },
                }
            },
        }
        body = _json(
            {
                "model": self.model_name,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": text},
                            {"type": "image_url", "image_url": {"url": image}},
                        ],
                    }
                ],
                "temperature": 0.1,
                "max_tokens": int(os.getenv("BEHAVIOR_SCENE_MAX_TOKENS", "512")),
                "response_format": {"type": "json_schema", "json_schema": {"name": "scene_regions", "strict": True, "schema": scene_schema}},
            }
        ).encode()
        req = urllib.request.Request(
            self.base_url + "/v1/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=180) as response:
            data = json.loads(response.read())
        raw = data["choices"][0]["message"]["content"]
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(parsed, dict) or not isinstance(parsed.get("regions"), list):
            raise RuntimeError("model returned malformed scene output")
        regions = parsed["regions"]
        _valid_scene({"approved": False, "regions": regions, "schedule": None})
        return regions


class BehaviorWorker:
    def __init__(self, store: Store, analyzer=None):
        self.store, self.analyzer = store, analyzer or TemporalAnalyzer()
        self.stop_event = threading.Event()
        self.thread = None
        self.model_lock = threading.Lock()

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(
            target=self.loop, daemon=True, name="behavior-worker"
        )
        self.thread.start()

    def loop(self):
        while not self.stop_event.is_set():
            row = self.store.one(
                "SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1"
            )
            if not row:
                self.stop_event.wait(0.2)
                continue
            self.run_job(row["id"])

    def cancelled(self, jid):
        r = self.store.one("SELECT cancelled FROM jobs WHERE id=?", (jid,))
        return not r or bool(r[0])

    def setjob(self, jid, **kw):
        values = {**kw, "updated_at": utcnow()}
        cols = ", ".join(f"{k}=?" for k in values)
        self.store.run(f"UPDATE jobs SET {cols} WHERE id=?", (*values.values(), jid))

    def run_job(self, jid):
        j = self.store.one("SELECT * FROM jobs WHERE id=?", (jid,))
        if not j or self.cancelled(jid):
            self.setjob(jid, status="cancelled", stage="cancelled")
            return
        self.setjob(jid, status="processing", stage="preparing", progress=2)
        try:
            if j["type"] == "scene":
                self._scene(j)
                return
            self._analysis(j)
        except Exception as e:
            self.setjob(jid, status="error", stage="error", error=str(e), progress=100)
            if j["type"] == "scene":
                self.store.run(
                    "UPDATE videos SET scene_status='error',scene_error=? WHERE id=?",
                    (str(e), j["video_id"]),
                )
            else:
                self.store.run(
                    "UPDATE analysis_windows SET status='error' "
                    "WHERE job_id=? AND status NOT IN ('done','error')",
                    (jid,),
                )
                self.store.run(
                    "UPDATE videos SET status='error',error=? WHERE id=?",
                    (str(e), j["video_id"]),
                )

    def _scene(self, j):
        # Suggestions are generated from representative video evidence; without a dedicated
        # scene model this remains an explicit error rather than fabricated geometry.
        v = self.store.one("SELECT * FROM videos WHERE id=?", (j["video_id"],))
        version = v["scene_version"]
        self.store.run(
            "UPDATE videos SET scene_status='processing',scene_error=NULL WHERE id=?",
            (v["id"],),
        )
        self.setjob(j["id"], stage="scene_suggest", progress=30)
        if self.cancelled(j["id"]):
            self.setjob(j["id"], status="cancelled", stage="cancelled", progress=100)
            return
        try:
            if not hasattr(self.analyzer, "suggest_scene"):
                raise RuntimeError("Scene suggestion model is unavailable")
            regions = self.analyzer.suggest_scene(Path(v["frame_path"]))
            if self.cancelled(j["id"]):
                self.setjob(
                    j["id"], status="cancelled", stage="cancelled", progress=100
                )
                return
            cur = self.store.one(
                "SELECT scene_version,scene_approved FROM videos WHERE id=?", (v["id"],)
            )
            if cur["scene_version"] == version and not cur["scene_approved"]:
                self.store.run(
                    "UPDATE videos SET scene_status='suggested',regions=? WHERE id=?",
                    (_json(regions), v["id"]),
                )
            self.setjob(j["id"], status="done", stage="done", progress=100)
        except Exception as exc:
            cur = self.store.one(
                "SELECT scene_version,scene_approved FROM videos WHERE id=?", (v["id"],)
            )
            if cur and cur["scene_version"] == version and not cur["scene_approved"]:
                self.store.run(
                    "UPDATE videos SET scene_status='error',scene_error=? WHERE id=?",
                    (str(exc), v["id"]),
                )
            self.setjob(
                j["id"], status="error", stage="error", error=str(exc), progress=100
            )

    def _analysis(self, j):
        v = self.store.one("SELECT * FROM videos WHERE id=?", (j["video_id"],))
        # Restart recovery reruns a job from the beginning; do not duplicate its
        # previous partial trace. A new reanalysis job retains earlier traces.
        self.store.run("DELETE FROM candidate_traces WHERE job_id=?", (j["id"],))
        self.store.run("DELETE FROM analysis_windows WHERE job_id=?", (j["id"],))
        path = Path(v["path"])
        self.store.run(
            "UPDATE videos SET status='processing',error=NULL WHERE id=?", (v["id"],)
        )
        self.setjob(j["id"], stage="normalizing", progress=1)
        normalized = self.store.media / f"{v['id']}.mp4"
        if path != normalized:
            try:
                temporary = self.store.media / f"{v['id']}.normalizing.mp4"
                subprocess.run(
                    [
                        "ffmpeg",
                        "-y",
                        "-i",
                        str(path),
                        "-map",
                        "0:v:0?",
                        "-map",
                        "0:a?",
                        "-vf",
                        "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                        "-c:v",
                        "libx264",
                        "-crf",
                        "23",
                        "-pix_fmt",
                        "yuv420p",
                        "-c:a",
                        "aac",
                        "-movflags",
                        "+faststart",
                        str(temporary),
                    ],
                    capture_output=True,
                    check=True,
                )
                if not temporary.is_file() or not temporary.stat().st_size:
                    raise RuntimeError("normalization produced no media")
                temporary.replace(normalized)
                original = path
                path = normalized
                self.store.run(
                    "UPDATE videos SET path=? WHERE id=?", (str(path), v["id"])
                )
                original.unlink(missing_ok=True)
                v = self.store.one("SELECT * FROM videos WHERE id=?", (v["id"],))
            except Exception as exc:
                raise RuntimeError(f"video normalization failed: {exc}") from exc
        duration = float(v["duration_s"] or self.analyzer.metadata(path))
        self.store.run("UPDATE videos SET duration_s=? WHERE id=?", (duration, v["id"]))
        windows = [
            (round(s, 3), round(min(s + WINDOW_S, duration), 3))
            for s in _starts(duration)
        ]
        self.setjob(j["id"], stage="tracking", progress=5)
        try:
            with self.model_lock:
                track_observations = self.analyzer.track_video(
                    path, self.store.frames / f"{v['id']}-tracks", duration
                )
        except Exception as exc:
            raise RuntimeError(f"person tracking failed: {exc}") from exc
        proposed = []
        scene = self.store.scene(v)
        capture_context = {"captured_at": v["captured_at"], "timezone": v["timezone"]}
        if not v["captured_at"] or not scene["approved"]:
            scene = {**scene, "schedule": None}
        for n, (start, end) in enumerate(windows):
            if self.cancelled(j["id"]):
                self.setjob(
                    j["id"], status="cancelled", stage="cancelled", progress=100
                )
                self.store.run(
                    "UPDATE videos SET status='cancelled' WHERE id=?", (v["id"],)
                )
                return
            self.setjob(
                j["id"],
                stage="sampling",
                progress=5 + int(80 * n / max(1, len(windows))),
            )
            folder = self.store.frames / f"{v['id']}-{n}"
            model_name = getattr(self.analyzer, "model_name", "unknown")
            self.store.run(
                "INSERT OR REPLACE INTO analysis_windows "
                "(job_id,video_id,window_index,start_s,end_s,frame_count,status,candidate_count,model,config_version) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (j["id"], v["id"], n, start, end, 0, "sampling", 0, model_name, CONFIG_VERSION),
            )
            frames = self.analyzer.frames(path, folder, start, end)
            if not frames:
                raise RuntimeError(
                    f"could not sample video window {start:.2f}-{end:.2f}s"
                )
            window_tracks = [
                o for o in track_observations if start <= o["time_s"] <= end
            ]
            if FOCUS_VIEW:
                from .views import context_detail_frames
                frames = context_detail_frames(frames, window_tracks)
            self.store.run(
                "UPDATE analysis_windows SET frame_count=?,status='inference' "
                "WHERE job_id=? AND window_index=?",
                (len(frames), j["id"], n),
            )
            try:
                with self.model_lock:
                    observations = self.analyzer.infer(
                        frames, start, end, scene, window_tracks, capture_context
                    )
            except Exception:
                self.store.run(
                    "UPDATE analysis_windows SET status='error' WHERE job_id=? AND window_index=?",
                    (j["id"], n),
                )
                raise
            if not isinstance(observations, list):
                raise RuntimeError("model returned malformed event output")
            self.store.run(
                "UPDATE analysis_windows SET candidate_count=? WHERE job_id=? AND window_index=?",
                (len(observations), j["id"], n),
            )
            for x in observations:
                if not isinstance(x, dict):
                    raise RuntimeError("model returned malformed event")
                required = ("start_s", "end_s", "action", "description", "evidence", "uncertainty")
                if any(key not in x for key in required):
                    raise RuntimeError("model returned an incomplete event")
                if not isinstance(x["description"], str) or not x["description"].strip():
                    raise RuntimeError("model returned an event without a description")
                if (not isinstance(x["evidence"], list) or not x["evidence"] or any(not isinstance(item, str) or not item.strip() for item in x["evidence"])):
                    raise RuntimeError("model returned invalid event evidence")
                if not isinstance(x["uncertainty"], str):
                    raise RuntimeError("model returned invalid event uncertainty")
                try:
                    a = max(start, float(x.get("start_s", start)))
                    b = min(end, float(x.get("end_s", end)))
                except (TypeError, ValueError) as exc:
                    raise RuntimeError(
                        "model returned invalid event timestamps"
                    ) from exc
                if not math.isfinite(a) or not math.isfinite(b) or b <= a:
                    raise RuntimeError("model returned invalid event span")
                action = str(x.get("action", ""))
                if action not in {
                    "climbing",
                    "boundary_entry",
                    "access_interaction",
                    "object_tampering",
                    "other_observable_event",
                }:
                    raise RuntimeError("model returned an invalid action tag")
                reason = _candidate_rejection_reason(x)
                self.store.run(
                    "INSERT INTO candidate_traces VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex, j["id"], v["id"], n, a, b, action,
                     x["description"], _json(x["evidence"]), x["uncertainty"],
                     "rejected" if reason else "accepted", reason or "", getattr(self.analyzer, "model_name", "unknown"),
                     CONFIG_VERSION, utcnow()),
                )
                if reason:
                    continue
                # Tracks provide optional temporal context.  They do not establish
                # involvement, so only overlapping IDs are listed and descriptions
                # must remain grounded in the VLM's observable evidence.
                tracks = sorted(
                    {o["track_id"] for o in track_observations if a <= o["time_s"] <= b}
                )
                proposed.append(
                    {
                        "start_s": a,
                        "end_s": b,
                        "action": action,
                        "description": str(x.get("description", ""))[:2000],
                        "evidence": x.get("evidence", []),
                        "uncertainty": str(
                            x.get(
                                "uncertainty", "Model interpretation may be incomplete."
                            )
                        )[:1000],
                        "track_ids": tracks,
                    }
                )
            self.store.run(
                "UPDATE analysis_windows SET status='done' WHERE job_id=? AND window_index=?",
                (j["id"], n),
            )
        for event in _merge(proposed):
            self._save_event(v, event)
        self.store.run("UPDATE videos SET status='done' WHERE id=?", (v["id"],))
        self.setjob(j["id"], status="done", stage="done", progress=100)

    def _save_event(self, v, e):
        eid = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"behavior:{v['id']}:{e['action'].strip().lower()}:{e['start_s']:.3f}:{e['end_s']:.3f}",
        ).hex
        clip = self.store.clips / f"{eid}.mp4"
        # H.264 compressed 5 sec context, bounded at source limits.
        start = max(0, e["start_s"] - 2.5)
        end = min(float(v["duration_s"]), e["end_s"] + 2.5)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(start),
                "-to",
                str(end),
                "-i",
                v["path"],
                "-vf",
                "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                "-c:v",
                "libx264",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(clip),
            ],
            capture_output=True,
            check=True,
        )
        self.store.run(
            "INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                eid,
                v["id"],
                e["start_s"],
                e["end_s"],
                e["action"],
                e["description"],
                _json(e["evidence"] if isinstance(e["evidence"], list) else []),
                e["uncertainty"],
                _json(e["track_ids"]),
                "unreviewed",
                0,
                "",
                str(clip),
                getattr(self.analyzer, "model_name", "unknown"),
                config_version(),
            ),
        )
        self.store.run(
            "INSERT OR IGNORE INTO outbox VALUES (?,?,?,?,?)",
            (uuid.uuid4().hex, eid, utcnow(), "pending_integration", e["action"]),
        )


def _starts(duration):
    if duration <= WINDOW_S:
        return [0.0]
    x = 0.0
    out = []
    while x < duration:
        out.append(x)
        if x + WINDOW_S >= duration:
            break
        x += STRIDE_S
    return out


def _has_non_routine_evidence(event: dict) -> bool:
    return _candidate_rejection_reason(event) is None


def _candidate_rejection_reason(event: dict) -> str | None:
    """Require an observable action, not a model label for ordinary presence.

    This is a conservative alert gate, not a classifier. It cannot establish
    intent or recover a missed action; novel actions need human review and an
    explicit evidence rule before they are allowed to create notifications.
    """
    action = event["action"]
    words = " ".join([event["description"], *event["evidence"]]).lower()
    force = r"\b(?:pry|pries|pried|prying|break|breaking|broke|smash\w*|damag\w*|strik\w*|struck|forc\w*|cut\w*|shatter\w*)\b"
    if action == "climbing":
        return None
    if action == "boundary_entry":
        barrier = re.search(r"\b(?:fence|wall|barrier|window|gate)\b", words)
        crossing = re.search(r"\b(?:cross\w*|climb\w*|vault\w*|crawl\w*|squeez\w*|(?:pass\w*|enter\w*) (?:over|through))\b", words)
        return None if barrier and crossing else "no_visible_barrier_crossing"
    if action == "access_interaction":
        access = re.search(r"\b(?:door|window|gate|lock|fence|vehicle|car|van|truck)\b", words)
        repeated = re.search(r"\b(?:repeated\w*|multiple|several)\b.{0,35}\b(?:pull\w*|push\w*|attempt\w*|try|tries|tried|trying)\b", words)
        return None if access and (re.search(force, words) or repeated) else "no_forceful_access_attempt"
    if action == "object_tampering":
        return None if re.search(force, words) else "no_visible_object_damage"
    if action == "other_observable_event":
        return None if re.search(r"\b(?:fell|fall\w*|collaps\w*|punch\w*|kick\w*|fight\w*|struck|drag\w*)\b", words) else "no_specific_physical_incident"
    return "unsupported_action"


def _merge(events, gap=None):
    gap = MERGE_GAP_S if gap is None else gap
    out = []
    for e in sorted(events, key=lambda x: (x["action"].lower(), x["start_s"])):
        prev = next(
            (
                x
                for x in reversed(out)
                if x["action"].lower() == e["action"].lower()
                and e["start_s"] <= x["end_s"] + gap
            ),
            None,
        )
        if prev:
            prev["end_s"] = max(prev["end_s"], e["end_s"])
            prev["track_ids"] = sorted(set(prev["track_ids"] + e["track_ids"]))
            prev["evidence"] = (prev["evidence"] + e["evidence"])[:20]
        else:
            out.append(e)
    return sorted(out, key=lambda x: x["start_s"])


def _valid_scene(data):
    if not isinstance(data, dict) or not isinstance(data.get("approved"), bool):
        raise ValueError("approved must be boolean")
    regions = data.get("regions", [])
    if not isinstance(regions, list):
        raise ValueError("regions must be a list")
    for r in regions:
        if (
            not isinstance(r, dict)
            or r.get("kind") not in {"fence", "entrance", "restricted", "other"}
            or not isinstance(r.get("id"), str)
            or not isinstance(r.get("label"), str)
        ):
            raise ValueError("invalid region")
        pts = r.get("points")
        if (
            not isinstance(pts, list)
            or len(pts) < 3
            or any(
                not isinstance(p, list)
                or len(p) != 2
                or any(not isinstance(x, (int, float)) or x < 0 or x > 1 for x in p)
                for p in pts
            )
        ):
            raise ValueError("region points must be normalized polygons")
    schedule = data.get("schedule")
    if schedule is not None:
        if not isinstance(schedule, dict) or not all(
            isinstance(schedule.get(k), str) for k in ("start", "end", "timezone")
        ):
            raise ValueError("invalid schedule")
        try:
            ZoneInfo(schedule["timezone"])
        except Exception:
            raise ValueError("invalid schedule timezone")
        try:
            datetime.strptime(schedule["start"], "%H:%M")
            datetime.strptime(schedule["end"], "%H:%M")
        except ValueError:
            raise ValueError("schedule times must be HH:MM")
    return regions, schedule


def create_app(config: dict | None = None) -> Flask:
    config = config or {}
    root = Path(config.get("BEHAVIOR_RUNTIME", Path("runtime") / "behavior"))
    store = config.get("BEHAVIOR_STORE") or Store(root)
    worker = BehaviorWorker(store, config.get("BEHAVIOR_ANALYZER"))
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.config.update(
        MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES,
        BEHAVIOR_STORE=store,
        BEHAVIOR_WORKER=worker,
    )

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_):
        return json_error("video too large", 413)

    @app.errorhandler(HTTPException)
    def http_error(exc):
        return json_error(exc.description, exc.code or 500)

    bp = behavior_blueprint(store, worker)
    app.register_blueprint(bp)

    @app.get("/")
    def home():
        return redirect("/review")

    @app.get("/review")
    def review():
        from flask import render_template

        return render_template("review.html")

    if config.get("BEHAVIOR_START_WORKER", True):
        worker.start()
    return app


def behavior_blueprint(store: Store, worker: BehaviorWorker):
    bp = Blueprint("behavior", __name__)

    def detail(vid):
        v = store.one("SELECT * FROM videos WHERE id=?", (vid,))
        if not v:
            return None
        j = store.one(
            "SELECT * FROM jobs WHERE video_id=? AND type='analysis' ORDER BY created_at DESC LIMIT 1",
            (vid,),
        )
        es = store.all("SELECT * FROM events WHERE video_id=? ORDER BY start_s", (vid,))
        return {
            "video": store.video(v),
            "job": store.job(j) if j else None,
            "events": [event_obj(x) for x in es],
            "scene": store.scene(v),
        }

    @bp.post("/api/videos")
    def upload():
        state = store.cleanup()
        if state["paused"]:
            return json_error(state["message"], 507)
        f = request.files.get("video")
        if not f or not f.filename:
            return json_error("video is required")
        name = secure_filename(f.filename)
        ext = Path(name).suffix.lower()
        if not name or ext not in ALLOWED_EXTENSIONS:
            return json_error("unsupported video type")
        captured = request.form.get("captured_at") or None
        tz = request.form.get("timezone") or None
        if captured:
            try:
                d = datetime.fromisoformat(captured.replace("Z", "+00:00"))
                if d.tzinfo is None:
                    raise ValueError()
                captured = d.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            except ValueError:
                return json_error("captured_at must be ISO8601 with offset")
        if tz:
            try:
                ZoneInfo(tz)
            except Exception:
                return json_error("invalid timezone")
        vid = uuid.uuid4().hex
        path = store.media / f"{vid}.source{ext}"
        f.save(path)
        if path.stat().st_size > MAX_UPLOAD_BYTES:
            path.unlink(missing_ok=True)
            return json_error("video too large", 413)
        try:
            duration = worker.analyzer.metadata(path)
        except Exception as e:
            path.unlink(missing_ok=True)
            return json_error(f"invalid video: {e}")
        # representative frame with no user controlled command string
        frame = store.frames / f"{vid}.jpg"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(min(duration / 2, 2)),
                "-i",
                str(path),
                "-frames:v",
                "1",
                str(frame),
            ],
            capture_output=True,
        )
        now = utcnow()
        jid = uuid.uuid4().hex
        store.run(
            "INSERT INTO videos VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                vid,
                name,
                now,
                captured,
                tz,
                duration,
                "queued",
                None,
                str(path),
                str(frame),
                "empty",
                0,
                "[]",
                None,
                None,
                0,
            ),
        )
        store.run(
            "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)",
            (jid, vid, "analysis", "queued", 0, "queued", None, now, now, 0),
        )
        return jsonify(
            {
                "video": store.video(
                    store.one("SELECT * FROM videos WHERE id=?", (vid,))
                ),
                "job": store.job(store.one("SELECT * FROM jobs WHERE id=?", (jid,))),
            }
        ), 202

    @bp.get("/api/videos")
    def videos():
        return jsonify(
            {
                "videos": [
                    store.video(r)
                    for r in store.all("SELECT * FROM videos ORDER BY created_at DESC")
                ]
            }
        )

    @bp.get("/api/videos/<vid>")
    def get_video(vid):
        d = detail(vid)
        return jsonify(d) if d else json_error("video not found", 404)

    @bp.get("/api/videos/<vid>/media")
    def media(vid):
        # Hold the same lock as cleanup through send_file's path open.  Its response
        # retains the descriptor, so a later eviction cannot invalidate playback.
        with store.lock:
            v = store.one("SELECT * FROM videos WHERE id=?", (vid,))
            if not v or v["status"] == "evicted" or not Path(v["path"]).is_file():
                return json_error("media not found", 404)
            if Path(v["path"]).name != f"{vid}.mp4":
                return json_error("media is preparing", 409)
            return send_file(Path(v["path"]).resolve(), conditional=True, mimetype="video/mp4")

    @bp.get("/api/videos/<vid>/frame")
    def frame(vid):
        v = store.one("SELECT * FROM videos WHERE id=?", (vid,))
        if not v or not v["frame_path"] or not Path(v["frame_path"]).is_file():
            return json_error("frame not found", 404)
        return send_file(Path(v["frame_path"]).resolve(), mimetype="image/jpeg")

    @bp.post("/api/videos/<vid>/cancel")
    def cancel(vid):
        if not store.one("SELECT 1 FROM videos WHERE id=?", (vid,)):
            return json_error("video not found", 404)
        store.run(
            "UPDATE jobs SET cancelled=1,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END,stage=CASE WHEN status='queued' THEN 'cancelled' ELSE stage END,updated_at=? WHERE video_id=? AND status IN ('queued','processing')",
            (utcnow(), vid),
        )
        store.run(
            "UPDATE videos SET status='cancelled' WHERE id=? AND status='queued' AND EXISTS (SELECT 1 FROM jobs WHERE video_id=? AND type='analysis' AND status='cancelled')",
            (vid, vid),
        )
        return jsonify(detail(vid))

    @bp.post("/api/videos/<vid>/analyze")
    def analyze(vid):
        v = store.one("SELECT * FROM videos WHERE id=?", (vid,))
        if not v:
            return json_error("video not found", 404)
        if store.one(
            "SELECT 1 FROM events WHERE video_id=? AND (pinned=1 OR review_status='confirmed' OR correction<>'')",
            (vid,),
        ):
            return json_error("protected events prevent reanalysis", 409)
        if store.one(
            "SELECT 1 FROM jobs WHERE video_id=? AND type='analysis' AND status IN ('queued','processing')",
            (vid,),
        ):
            return json_error("analysis already active", 409)
        store.run(
            "DELETE FROM outbox WHERE event_id IN (SELECT id FROM events WHERE video_id=?)",
            (vid,),
        )
        store.run("DELETE FROM events WHERE video_id=?", (vid,))
        jid = uuid.uuid4().hex
        now = utcnow()
        store.run(
            "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)",
            (jid, vid, "analysis", "queued", 0, "queued", None, now, now, 0),
        )
        return jsonify(
            {"job": store.job(store.one("SELECT * FROM jobs WHERE id=?", (jid,)))}
        ), 202

    @bp.post("/api/videos/<vid>/scene/suggest")
    def scene_suggest(vid):
        v = store.one("SELECT * FROM videos WHERE id=?", (vid,))
        if not v:
            return json_error("video not found", 404)
        if v["scene_status"] in ("queued", "processing"):
            return json_error("scene suggestion already active", 409)
        jid = uuid.uuid4().hex
        now = utcnow()
        store.run(
            "UPDATE videos SET scene_status='queued',scene_error=NULL WHERE id=?",
            (vid,),
        )
        store.run(
            "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)",
            (jid, vid, "scene", "queued", 0, "queued", None, now, now, 0),
        )
        return jsonify(
            {"job": store.job(store.one("SELECT * FROM jobs WHERE id=?", (jid,)))}
        ), 202

    @bp.put("/api/videos/<vid>/scene")
    def scene_put(vid):
        v = store.one("SELECT * FROM videos WHERE id=?", (vid,))
        if not v:
            return json_error("video not found", 404)
        try:
            regions, schedule = _valid_scene(request.get_json(force=True))
        except Exception as e:
            return json_error(str(e))
        store.run(
            "UPDATE videos SET scene_status=?,scene_approved=?,regions=?,schedule=?,scene_error=NULL,scene_version=scene_version+1 WHERE id=?",
            (
                "approved" if request.json["approved"] else "suggested",
                int(request.json["approved"]),
                _json(regions),
                _json(schedule) if schedule else None,
                vid,
            ),
        )
        return jsonify(
            store.scene(store.one("SELECT * FROM videos WHERE id=?", (vid,)))
        )

    @bp.patch("/api/events/<eid>")
    def event_patch(eid):
        e = store.one("SELECT * FROM events WHERE id=?", (eid,))
        if not e:
            return json_error("event not found", 404)
        data = request.get_json(force=True)
        allowed = {"review_status", "pinned", "correction"}
        if not isinstance(data, dict) or any(k not in allowed for k in data):
            return json_error("invalid event update")
        if "review_status" in data and data["review_status"] not in {
            "unreviewed",
            "confirmed",
            "dismissed",
        }:
            return json_error("invalid review_status")
        if "pinned" in data and not isinstance(data["pinned"], bool):
            return json_error("pinned must be boolean")
        if "correction" in data and not isinstance(data["correction"], str):
            return json_error("correction must be string")
        if data:
            store.run(
                "UPDATE events SET "
                + ", ".join(f"{k}=?" for k in data)
                + " WHERE id=?",
                (*data.values(), eid),
            )
        return jsonify(event_obj(store.one("SELECT * FROM events WHERE id=?", (eid,))))

    @bp.get("/api/events/<eid>/clip")
    def clip(eid):
        e = store.one("SELECT * FROM events WHERE id=?", (eid,))
        if not e or not e["clip_path"] or not Path(e["clip_path"]).is_file():
            return json_error("clip not found", 404)
        return send_file(Path(e["clip_path"]).resolve(), conditional=True)

    @bp.get("/api/outbox")
    def outbox():
        return jsonify(
            {
                "items": [
                    dict(r)
                    for r in store.all("SELECT * FROM outbox ORDER BY created_at DESC")
                ]
            }
        )

    @bp.get("/api/system")
    def system():
        s = store.cleanup()
        return jsonify(
            {
                "storage": s,
                "worker": {"alive": bool(worker.thread and worker.thread.is_alive())},
                "model": getattr(worker.analyzer, "model_name", "unknown"),
                # Zero-event runs still need to record which variant produced them.
                "config_version": config_version(),
            }
        )

    return bp


def event_obj(e):
    return {
        "id": e["id"],
        "video_id": e["video_id"],
        "start_s": e["start_s"],
        "end_s": e["end_s"],
        "action": e["action"],
        "description": e["description"],
        "evidence": json.loads(e["evidence"] or "[]"),
        "uncertainty": e["uncertainty"],
        "track_ids": json.loads(e["track_ids"] or "[]"),
        "review_status": e["review_status"],
        "pinned": bool(e["pinned"]),
        "correction": e["correction"],
        "clip_url": f"/api/events/{e['id']}/clip",
        "model": e["model"],
        "config_version": e["config_version"],
    }
