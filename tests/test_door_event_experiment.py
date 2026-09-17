import unittest

import behavior
from behavior import _candidate_rejection_reason
from evaluation.door_event_experiment import (
    DOOR_CONTACT_PROMPT,
    OFFLINE_CONFIG_VERSION,
    build_plan,
)


class DoorPlanTests(unittest.TestCase):
    def manifest(self):
        return {
            "clips": [
                {
                    "id": "uca-04",
                    "source_id": "Vandalism034",
                    "label_status": "reviewed",
                    "events": [
                        {
                            "action": "object_tampering",
                            "start_s": 39.6,
                            "end_s": 58.0,
                        }
                    ],
                },
                {
                    "id": "uca-05",
                    "source_id": "Normal_Videos609",
                    "label_status": "reviewed",
                    "duration_s": 20.108,
                    "events": [],
                },
                {
                    "id": "uca-06",
                    "source_id": "Normal_Videos610",
                    "label_status": "reviewed",
                    "duration_s": 28.096,
                    "events": [],
                },
            ]
        }

    def test_plan_keeps_positive_and_controls_separate(self):
        plan = build_plan(self.manifest())
        self.assertEqual(plan["policy"], "door-contact-v1")
        self.assertEqual(plan["sampling"], {"fps": 2.0, "window_s": 8.0, "stride_s": 4.0})
        self.assertEqual(plan["positive"][0]["start_s"], 39.6)
        self.assertEqual([c["clip_id"] for c in plan["ordinary_controls"]], ["uca-05", "uca-06"])
        self.assertEqual(plan["ordinary_door_use_control"]["status"], "unavailable")

    def test_plan_rejects_changed_or_unreviewed_positive(self):
        manifest = self.manifest()
        manifest["clips"][0]["label_status"] = "unreviewed"
        with self.assertRaises(ValueError):
            build_plan(manifest)

        manifest = self.manifest()
        manifest["clips"][0]["source_id"] = "different-source"
        with self.assertRaisesRegex(ValueError, "source_id changed"):
            build_plan(manifest)

    def test_plan_records_publisher_door_control_when_supplied(self):
        door_manifest = {
            "clips": [
                {
                    "id": "meva-door-open-01",
                    "source_id": "2018-03-09.10-30-00.10-35-00.hospital.G479",
                    "label_status": "reviewed",
                    "duration_s": 10.0,
                    "source_crop_s": [286.0, 296.0],
                    "sha256": "9907eda1431de30cb3f83b5443f230b3b8fed4c8088c8b1e84167f833d217f21",
                    "annotation_sha256": "a714ffa60d085e845aa55abe481bd1cd95de7977b9dd8ff56be204d173d66647",
                    "events": [],
                    "label_note": "publisher opens facility door",
                }
            ]
        }
        plan = build_plan(self.manifest(), door_manifest=door_manifest)
        self.assertEqual(plan["ordinary_door_use_control"]["status"], "available")

        door_manifest["clips"][0]["source_crop_s"] = [0.0, 10.0]
        with self.assertRaisesRegex(ValueError, "pinned 286-296"):
            build_plan(self.manifest(), door_manifest=door_manifest)


class DoorPromptTests(unittest.TestCase):
    def test_offline_prompt_requires_separated_contacts_and_rejects_routine_use(self):
        self.assertIn("at least two distinct, clearly visible contact moments", DOOR_CONTACT_PROMPT)
        self.assertIn("visible release or withdrawal", DOOR_CONTACT_PROMPT)
        self.assertIn("adjacent frames from one sustained contact count as one moment", DOOR_CONTACT_PROMPT)
        self.assertIn("routine opening/closing", DOOR_CONTACT_PROMPT)
        self.assertIn('return exactly {"events":[]}', DOOR_CONTACT_PROMPT)
        self.assertIn("Do not infer identity, intent, guilt, authorization", DOOR_CONTACT_PROMPT)

    def test_production_policy_has_no_door_contact_variant(self):
        self.assertEqual(behavior.config_version(), "temporal-v3-bounded-evidence2")
        self.assertEqual(OFFLINE_CONFIG_VERSION, "temporal-v4-door-contact-evidence2")

    def test_existing_gate_still_rejects_routine_door_use(self):
        routine = {
            "action": "access_interaction",
            "description": "A person opens and closes a door once.",
            "evidence": ["The person uses the door handle."],
        }
        self.assertEqual(_candidate_rejection_reason(routine), "no_forceful_access_attempt")


if __name__ == "__main__":
    unittest.main()
