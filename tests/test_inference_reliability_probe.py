import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import inference_reliability_probe as probe


class InferenceReliabilityProbeTests(unittest.TestCase):
    def test_checkpoint_replaces_a_complete_safe_record(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            probe._checkpoint(output, {"status": "running", "sequence": []})
            self.assertEqual(json.loads(output.read_text())["status"], "running")
            self.assertFalse(output.with_suffix(".json.part").exists())

    def test_json_object_probe_keeps_the_application_validator(self):
        captured = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"model":"test","choices":[{"finish_reason":"stop","message":{"content":"{\\"events\\":[]}"}}]}'

        def request(request, timeout):
            captured.append(json.loads(request.data))
            return Response()

        with patch("urllib.request.urlopen", side_effect=request):
            self.assertEqual(probe.JsonObjectAnalyzer().infer([], 0, 8, {}), [])
        self.assertEqual(captured[0]["response_format"], {"type": "json_object"})

    def test_contract_variant_adds_fields_without_relaxing_json_object_format(self):
        captured = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"model":"test","choices":[{"finish_reason":"stop","message":{"content":"{\\"events\\":[]}"}}]}'

        def request(request, timeout):
            captured.append(json.loads(request.data))
            return Response()

        with patch("urllib.request.urlopen", side_effect=request):
            self.assertEqual(probe.JsonObjectAnalyzer(include_contract=True).infer([], 0, 8, {}), [])
        self.assertEqual(captured[0]["response_format"], {"type": "json_object"})
        self.assertIn("evidence (array of one to three non-empty strings)", captured[0]["messages"][0]["content"][0]["text"])

    def test_output_shape_excludes_model_strings_but_records_invalid_evidence(self):
        shape = probe._output_shape(
            {"events": [{"evidence": ["private model text"], "description": "also private"}]}
        )
        self.assertEqual(shape["events"][0]["evidence_count"], 1)
        self.assertEqual(shape["events"][0]["evidence_string_lengths"], [18])
        self.assertNotIn("private model text", str(shape))
