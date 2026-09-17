"""Focused guards for the boundary dashboard's browser/API contract."""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_JS = (ROOT / "static" / "review.js").read_text()
REVIEW_HTML = (ROOT / "templates" / "review.html").read_text()


class ReviewDashboardUiTests(unittest.TestCase):
    def test_camera_contract_uses_nested_preview_and_explicit_proposal_status(self):
        self.assertIn("camera&&camera.preview", REVIEW_JS)
        self.assertIn("revision.status==='proposed'", REVIEW_JS)
        self.assertNotIn("detail&&detail.preview", REVIEW_JS)

    def test_monitor_acknowledgement_requires_visible_provenance_candidate(self):
        self.assertIn("provenance.camera_id", REVIEW_JS)
        self.assertIn("monitorVisible()", REVIEW_JS)
        self.assertIn("IntersectionObserver", REVIEW_JS)
        self.assertNotIn("acknowledgeRenderedEvent", REVIEW_JS)

    def test_test_controls_keep_scoring_and_export_after_freeze(self):
        self.assertIn('id="test-session-actions"', REVIEW_HTML)
        self.assertIn('id="test-score"', REVIEW_HTML)
        self.assertIn('id="test-export"', REVIEW_HTML)
        self.assertIn("result.passed===true?'passed':result.passed===false?'not passed':'unknown'", REVIEW_JS)

    def test_remote_frame_errors_hide_images_and_valid_loads_restore_them(self):
        self.assertNotIn('id="monitor-frame"', REVIEW_HTML)
        self.assertNotIn('id="setup-reference-frame"', REVIEW_HTML)
        self.assertIn("monitorFrame.addEventListener('error'", REVIEW_JS)
        self.assertIn("setupReferenceFrame.addEventListener('error'", REVIEW_JS)
        self.assertIn("Latest preview frame could not be loaded.", REVIEW_JS)
        self.assertIn("Reference frame could not be loaded.", REVIEW_JS)
        self.assertIn("setupReferenceFrame.addEventListener('load'", REVIEW_JS)
        self.assertIn("renderLineDrawing();", REVIEW_JS)


if __name__ == "__main__":
    unittest.main()
