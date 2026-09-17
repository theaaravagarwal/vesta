"""Regression checks for the browser-only camera capture contract."""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_JS = (ROOT / "static" / "review.js").read_text()
REVIEW_HTML = (ROOT / "templates" / "review.html").read_text()


class ReviewCaptureUiTests(unittest.TestCase):
    def test_capture_is_opt_in_video_only_and_requires_a_secure_context(self):
        self.assertIn("window.isSecureContext", REVIEW_JS)
        self.assertIn("HTTPS Tailscale address (or localhost)", REVIEW_JS)
        self.assertEqual(REVIEW_JS.count("navigator.mediaDevices.getUserMedia"), 3)
        self.assertEqual(REVIEW_JS.count("audio:false"), 2)
        self.assertIn("el.webcamOpen.addEventListener('click',openWebcam)", REVIEW_JS)
        self.assertIn("el.cameraMonitorStart.addEventListener('click',startMonitor)", REVIEW_JS)

    def test_capture_copy_names_the_browser_device_and_remote_analysis_boundary(self):
        self.assertIn("this device's camera", REVIEW_HTML)
        self.assertIn("no analysis computer's camera is opened", REVIEW_HTML)
        self.assertIn("private Tailscale dashboard address", REVIEW_HTML)


if __name__ == "__main__":
    unittest.main()
