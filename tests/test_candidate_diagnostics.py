import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from evaluation.export_candidates import export


class CandidateDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "behavior.sqlite3"
        conn = sqlite3.connect(self.db)
        conn.executescript(
            """
            CREATE TABLE videos (id TEXT PRIMARY KEY, name TEXT, status TEXT, duration_s REAL);
            CREATE TABLE jobs (id TEXT PRIMARY KEY, video_id TEXT, type TEXT, status TEXT, error TEXT);
            CREATE TABLE analysis_windows (
                job_id TEXT, video_id TEXT, window_index INTEGER, start_s REAL, end_s REAL,
                frame_count INTEGER, status TEXT, candidate_count INTEGER
            );
            CREATE TABLE candidate_traces (
                id TEXT PRIMARY KEY, job_id TEXT, video_id TEXT, window_index INTEGER,
                start_s REAL, end_s REAL, action TEXT, description TEXT, evidence TEXT,
                uncertainty TEXT, decision TEXT, reason TEXT, model TEXT, config_version TEXT,
                created_at TEXT
            );
            """
        )
        conn.execute("INSERT INTO videos VALUES ('v','clip','ready',10)")
        conn.executemany("INSERT INTO jobs VALUES (?,?,?,?,?)", [
            ("done", "v", "analysis", "done", None),
            ("failed", "v", "analysis", "error", "decoder failed"),
        ])
        conn.executemany("INSERT INTO analysis_windows VALUES (?,?,?,?,?,?,?,?)", [
            ("done", "v", 0, 0, 2, 4, "done", 0),
            ("done", "v", 1, 2, 4, 4, "done", 2),
            ("failed", "v", 0, 4, 6, 0, "error", 0),
            ("failed", "v", 1, 6, 8, 0, "processing", 0),
        ])
        conn.executemany("INSERT INTO candidate_traces VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            ("c1", "done", "v", 1, 2, 3, "climbing", "x", "[\"fence\"]", "", "accepted", "physical", "m", "c", "now"),
            ("c2", "done", "v", 1, 3, 4, "other", "x", "[]", "", "rejected", "gate_rejected", "m", "c", "now"),
        ])
        conn.commit()
        conn.close()

    def tearDown(self):
        self.temp.cleanup()

    def test_summaries_distinguish_zero_model_output_and_gate_rejection(self):
        result = export(self.db, "v")
        by_job = {row["job_id"]: row for row in result["job_summaries"]}
        self.assertEqual(by_job["done"]["windows_zero_candidates"], 1)
        self.assertEqual(by_job["done"]["candidates_accepted"], 1)
        self.assertEqual(by_job["done"]["candidates_rejected"], 1)
        self.assertEqual(by_job["done"]["reason_counts"], {"gate_rejected": 1, "physical": 1})
        self.assertEqual(by_job["done"]["action_counts"], {"climbing": 1, "other": 1})
        self.assertEqual(by_job["done"]["trace_status"], "available")
        self.assertEqual(by_job["failed"]["windows_error"], 1)
        self.assertEqual(by_job["failed"]["windows_incomplete"], 1)
        self.assertEqual(by_job["failed"]["windows_zero_candidates"], 0)
        self.assertEqual(by_job["failed"]["status"], "error")
        self.assertEqual(by_job["failed"]["error"], "decoder failed")

    def test_old_database_reports_missing_trace_table(self):
        old = Path(self.temp.name) / "old.sqlite3"
        conn = sqlite3.connect(old)
        conn.execute("CREATE TABLE videos (id TEXT PRIMARY KEY, name TEXT, status TEXT, duration_s REAL)")
        conn.execute("INSERT INTO videos VALUES ('v','old','ready',1)")
        conn.execute("CREATE TABLE analysis_windows (job_id TEXT, video_id TEXT, window_index INTEGER, start_s REAL, end_s REAL, frame_count INTEGER, status TEXT, candidate_count INTEGER)")
        conn.execute("INSERT INTO analysis_windows VALUES ('j','v',0,0,1,1,'done',0)")
        conn.commit()
        conn.close()
        result = export(old, "v")
        summary = result["job_summaries"][0]
        self.assertEqual(summary["trace_status"], "unavailable")
        self.assertIn("candidate_traces", summary["trace_error"])

    def test_present_tables_with_no_historical_rows_are_not_recorded(self):
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO jobs VALUES ('empty','v','analysis','done',NULL)")
        conn.commit(); conn.close()
        result = export(self.db, "v")
        summary = next(row for row in result["job_summaries"] if row["job_id"] == "empty")
        self.assertEqual(summary["trace_status"], "not_recorded")

    def test_scene_jobs_are_excluded_from_analysis_summaries(self):
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO jobs VALUES ('scene','v','scene','done',NULL)")
        conn.execute("INSERT INTO analysis_windows VALUES ('scene','v',0,0,1,1,'done',0)")
        conn.commit(); conn.close()
        result = export(self.db, "v")
        self.assertNotIn("scene", {row["job_id"] for row in result["job_summaries"]})

    def test_cancelled_job_with_completed_prefix_is_partial(self):
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE jobs SET status='cancelled' WHERE id='done'")
        conn.commit(); conn.close()
        result = export(self.db, "v")
        summary = next(row for row in result["job_summaries"] if row["job_id"] == "done")
        self.assertEqual(summary["trace_status"], "partial")

    def test_traces_without_window_table_are_unavailable(self):
        old = Path(self.temp.name) / "trace-only.sqlite3"
        conn = sqlite3.connect(old)
        conn.executescript(
            """
            CREATE TABLE videos (id TEXT PRIMARY KEY, name TEXT, status TEXT, duration_s REAL);
            CREATE TABLE jobs (id TEXT PRIMARY KEY, video_id TEXT, type TEXT, status TEXT, error TEXT);
            CREATE TABLE candidate_traces (
                id TEXT PRIMARY KEY, job_id TEXT, video_id TEXT, window_index INTEGER,
                start_s REAL, end_s REAL, action TEXT, description TEXT, evidence TEXT,
                uncertainty TEXT, decision TEXT, reason TEXT, model TEXT, config_version TEXT,
                created_at TEXT
            );
            INSERT INTO videos VALUES ('v','clip','ready',1);
            INSERT INTO jobs VALUES ('j','v','analysis','done',NULL);
            INSERT INTO candidate_traces VALUES ('c','j','v',0,0,1,'x','x','[]','','accepted','','m','c','now');
            """
        )
        conn.commit(); conn.close()
        result = export(old, "v")
        summary = result["job_summaries"][0]
        self.assertEqual(summary["trace_status"], "unavailable")
        self.assertIn("analysis_windows", summary["trace_error"])

    def test_export_is_read_only(self):
        before = self.db.read_bytes()
        export(self.db, "v")
        self.assertEqual(self.db.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
