# Clip-level diagnosis, 2026-09-16

This note compares the **actual 2 fps JPEG frames supplied to the model**, not
just the source videos. It is a small diagnostic sample, not a campus accuracy
estimate.

| Clip/window | Frame observation | Model outcome |
| --- | --- | --- |
| Mobius `mobius-01`, 16–24 s | Person occupies a large portion of the image; legs and torso visibly move over the fence across consecutive frames. | Existing replay has `climbing` candidates at 16–23.5 s and 32–36 s. |
| UCA `uca-02`, 0–8 s | Empty passage in all 16 sampled frames despite a broad publisher event interval beginning near 0 s. | No action visible to infer in this window. |
| UCA `uca-02`, 36–44 s | Person traverses the passage and steps downward. The barrier crossing, if any, is not clear from these frames. | Existing replay called it `access_interaction`; a fresh current-model call returned no event. |
| UCA `uca-02`, 52–60 s | Only part of a person appears at the far right, partly occluded by the fence and frame edge. Body-over-barrier geometry is weak. | Existing replay did not produce `climbing`. |
| UCA `uca-05`, 0–8 s | Ordinary street scene with people near cars. | Existing replay produced generic presence and speculative car-interaction alerts. |

The strongest supported cause of the climbing miss is **visibility and action
ambiguity at the camera edge**, compounded by 2 fps sampling and a small 3B
vision model. Do not treat the broad UCA publisher interval as frame-level proof
that climbing is visible throughout. The original UCA run and Mobius run also
used different recorded prompt/config versions, so their scores are not a clean
model-only comparison. The optional context/detail focus experiment previously
increased alerts without recovering an exact climbing hit; it is not promoted.

The current evidence gate rejects `access_interaction` and
`other_observable_event` candidates about a vehicle when no forceful physical
action is described. It leaves other action classes, including `climbing`,
unchanged. Applied to **saved historical predictions**, it removes four alerts
from the two ordinary UCA controls (`uca-05`, `uca-06`) and one generic Mobius
alert while retaining both Mobius `climbing` alerts. This is a deterministic
offline comparison; it does not establish a new full-replay detection score.

Before camera integration, use a small held-out set with frame-accurate visible
action labels and ordinary controls from the intended camera geometry. Record
false alerts per camera-hour and exact action recall, and review cropped-edge
misses manually. The current UCA and Mobius samples are insufficient to claim
campus-ready detection.
