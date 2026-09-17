"""Bounded admission and durable provenance for browser camera chunks.

This is deliberately a local, single-service queue boundary.  A camera id is
stable browser metadata, never a filename or filesystem path, and a session id
only groups one opted-in monitoring run.  No identity or cross-camera matching
is derived from either value.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any


CAMERA_ID = re.compile(r"^cam_[A-Za-z0-9_-]{8,64}$")
SESSION_ID = re.compile(r"^sess_[A-Za-z0-9_-]{8,64}$")
CHUNK_ID = re.compile(r"^chunk_[A-Za-z0-9_-]{8,64}$")


class CameraAdmissionFull(Exception):
    """The durable camera queue reached a documented capacity."""

    def __init__(self, scope: str, retry_after: int):
        self.scope = scope
        self.retry_after = retry_after
        super().__init__(f"{scope} camera analysis queue is full")


def validate_camera_id(value: str | None) -> str:
    if not isinstance(value, str) or not CAMERA_ID.fullmatch(value):
        raise ValueError("camera_id must be a cam_ identifier of 12 to 68 safe characters")
    return value


def validate_session_id(value: str | None) -> str:
    if not isinstance(value, str) or not SESSION_ID.fullmatch(value):
        raise ValueError("capture_session_id must be a sess_ identifier of 13 to 69 safe characters")
    return value


def validate_chunk_id(value: str | None) -> str:
    if not isinstance(value, str) or not CHUNK_ID.fullmatch(value):
        raise ValueError("chunk_id must be a chunk_ identifier of 14 to 70 safe characters")
    return value


@dataclass(frozen=True)
class CameraQueueLimits:
    """Caps count durable queued/processing camera jobs, never browser buffers."""

    global_pending: int = 8
    per_camera_pending: int = 2
    retry_after_s: int = 10

    def __post_init__(self):
        if self.global_pending < 1 or self.per_camera_pending < 1 or self.retry_after_s < 1:
            raise ValueError("camera queue limits must be positive")


class CameraQueue:
    """Atomically reserve a camera slot and persist its video/job provenance."""

    def __init__(self, store: Any, limits: CameraQueueLimits):
        self.store = store
        self.limits = limits

    def receipt(self, camera_id: str, session_id: str, chunk_id: str):
        return self.store.one(
            "SELECT video_id,job_id FROM camera_chunk_receipts "
            "WHERE camera_id=? AND capture_session_id=? AND chunk_id=?",
            (camera_id, session_id, chunk_id),
        )

    def admit(
        self,
        *,
        video: tuple,
        job: tuple,
        camera_id: str,
        session_id: str,
        chunk_id: str,
        received_at: str,
    ) -> tuple[str, str, bool]:
        """Insert video and job in the same IMMEDIATE transaction as capacity checks."""
        with self.store.lock:
            conn = self.store.conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                existing = conn.execute(
                    "SELECT video_id,job_id FROM camera_chunk_receipts "
                    "WHERE camera_id=? AND capture_session_id=? AND chunk_id=?",
                    (camera_id, session_id, chunk_id),
                ).fetchone()
                if existing:
                    conn.commit()
                    return existing["video_id"], existing["job_id"], True
                global_count = conn.execute(
                    "SELECT count(*) FROM jobs j JOIN video_sources s ON s.video_id=j.video_id "
                    "WHERE s.source_kind='browser_camera' AND j.type='analysis' "
                    "AND j.status IN ('queued','processing')"
                ).fetchone()[0]
                camera_count = conn.execute(
                    "SELECT count(*) FROM jobs j JOIN video_sources s ON s.video_id=j.video_id "
                    "WHERE s.source_kind='browser_camera' AND s.camera_id=? AND j.type='analysis' "
                    "AND j.status IN ('queued','processing')",
                    (camera_id,),
                ).fetchone()[0]
                if global_count >= self.limits.global_pending:
                    self._record_error(conn, camera_id, session_id, received_at, "Camera queue is at global capacity")
                    raise CameraAdmissionFull("global", self.limits.retry_after_s)
                if camera_count >= self.limits.per_camera_pending:
                    self._record_error(conn, camera_id, session_id, received_at, "This camera queue is at capacity")
                    raise CameraAdmissionFull("camera", self.limits.retry_after_s)
                conn.execute("INSERT INTO videos VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", video)
                conn.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)", job)
                conn.execute(
                    "INSERT INTO video_sources (video_id,source_kind,camera_id,capture_session_id) VALUES (?,?,?,?)",
                    (video[0], "browser_camera", camera_id, session_id),
                )
                conn.execute(
                    "INSERT INTO camera_chunk_receipts "
                    "(camera_id,capture_session_id,chunk_id,video_id,job_id,received_at) VALUES (?,?,?,?,?,?)",
                    (camera_id, session_id, chunk_id, video[0], job[0], received_at),
                )
                conn.execute(
                    "INSERT INTO camera_sources (camera_id,created_at,last_received_at,last_error,last_session_id) "
                    "VALUES (?,?,?,?,?) ON CONFLICT(camera_id) DO UPDATE SET "
                    "last_received_at=excluded.last_received_at,last_error=NULL,last_session_id=excluded.last_session_id",
                    (camera_id, received_at, received_at, None, session_id),
                )
                conn.commit()
                return video[0], job[0], False
            except CameraAdmissionFull:
                # The rejected receipt is itself useful camera health data.
                conn.commit()
                raise
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    @staticmethod
    def _record_error(conn: sqlite3.Connection, camera_id: str, session_id: str, now: str, error: str) -> None:
        conn.execute(
            "INSERT INTO camera_sources (camera_id,created_at,last_received_at,last_error,last_session_id) "
            "VALUES (?,?,?,?,?) ON CONFLICT(camera_id) DO UPDATE SET "
            "last_error=excluded.last_error,last_session_id=excluded.last_session_id",
            (camera_id, now, None, error, session_id),
        )
