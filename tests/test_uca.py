import unittest
from evaluation.fetch_uca import adapt_events, duration_ok, validate_publisher_events


class UcaTests(unittest.TestCase):
    def test_crop_offsets_and_clamps_source_events(self):
        events = [(63.1, 83.1, "boundary_entry", "index 3")]
        self.assertEqual(
            adapt_events(events, (0, 70), 70),
            [
                {
                    "action": "boundary_entry",
                    "start_s": 63.1,
                    "end_s": 70.0,
                    "mapping_rationale": "index 3",
                }
            ],
        )
        self.assertEqual(
            adapt_events([(87.9, 127, "object_tampering", "i")], (80, 150), 70)[0][
                "start_s"
            ],
            7.9,
        )

    def test_indexed_annotation_pair_required(self):
        published = {"timestamps": [[0, 1], [4.9, 8.6], [9.3, 13.3]]}
        validate_publisher_events(
            "Burglary081", [(4.9, 8.6, "x", ""), (9.3, 13.3, "x", "")], published
        )
        with self.assertRaises(ValueError):
            validate_publisher_events(
                "Burglary081", [(4.9, 13.3, "x", ""), (9.3, 13.3, "x", "")], published
            )

    def test_duration_tolerance(self):
        self.assertTrue(duration_ok(20.5, 20.13))
        self.assertFalse(duration_ok(21, 20.13))


if __name__ == "__main__":
    unittest.main()
