import unittest
from behavior.views import focus_bounds


class FocusViewTests(unittest.TestCase):
    def test_missing_or_invalid_boxes_keep_context(self):
        self.assertIsNone(focus_bounds([]))
        self.assertIsNone(focus_bounds([{"normalized_box": [float("nan"), 0, 1, 1]}]))
        self.assertIsNone(focus_bounds([{"box": [5, 5, 20, 20]}]))

    def test_edges_are_clamped_and_context_is_padded(self):
        b = focus_bounds([{"normalized_box": [0, 0.3, 0.1, 0.6]}])
        self.assertEqual(b[0], 0)
        self.assertGreater(b[2], 0.1)
        self.assertLess(b[1], 0.3)
        self.assertGreater(b[3], 0.6)

    def test_spread_tracks_do_not_remove_scene(self):
        self.assertIsNone(
            focus_bounds(
                [
                    {"normalized_box": [0, 0, 0.1, 0.4]},
                    {"normalized_box": [0.9, 0.6, 1, 1]},
                ]
            )
        )
