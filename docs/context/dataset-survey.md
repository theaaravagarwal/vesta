# Broader dataset survey — 2026-09-15

This survey checks actual downloadable artifacts and annotation types, rather than
assuming a dataset title or vendor accuracy claim establishes suitability.

| Source | Verified contents/access | Fit for Vesta |
| --- | --- | --- |
| [Mobius fence climbing](https://www.mobiusi.com/datasets/53a20b76a58884184add70423f31a9cb) | Vendor advertises 500 records / 1.2GB, MP4/JSON. Full access requires a commercial authorization contract. One free sample verified. | Useful immediate visual diagnostic; full temporal labels and independent scene coverage not inspected. |
| [Corresponding HF sample](https://huggingface.co/datasets/shangzx/Fence-Climbing-Action-Recognition-Dataset) | One MP4 plus one 91-byte JSON metadata record, not a second independent dataset. Sample license CC-BY-NC-SA-4.0. | Downloaded and tested; metadata contains filename, duration and resolution, not action intervals. |
| [Kaggle UCF-Crime with Fence Climbing](https://www.kaggle.com/datasets/ibrahimarif31/ucf-crime-with-fence-climbing) | Card claims 400+ minutes of source footage. API file listing sampled JPEG frames in class folders; 233,240 train / 34,743 test images, about 4.05GB. | Frame-classification material. Raw temporal clips and event boundaries were not verified. Derivative source overlap with UCF is expected; exact mapping not supplied. |
| [Roboflow Climbing Fence](https://universe.roboflow.com/osborne-rowland-r4w5m/climbing-fence/dataset/1) | Image object detection, class boxes; v1 reports 247 images, 210/20/17 split. Overview separately shows 107 previews. CC BY 4.0 declared. | Useful for localized person/fence interaction experiments, not temporal behavior scoring. Adjacent frames/augmentation need source-group leakage checks. |
| [Ultralytics climbing-detectionv1iyolov8](https://platform.ultralytics.com/yu-haitao/datasets/climbing-detectionv1iyolov8) | Page metadata reports 512 images, 488/19/5 split and bounding boxes; license field null. No temporal labels or direct video download verified. | Still-image detector candidate; rights and source provenance need clarification before training use. |
| [NWPU Campus](https://campusvaa.github.io/) | Official academic benchmark: 16.29 hours, 28 anomaly categories including fence climbing; 305 train / 242 test videos, 76.6GB. Author links Google Drive/Baidu. | Strong campus-domain candidate with normal activity and scene-dependent events. Academic/noncommercial terms prohibit redistribution, dataset derivation and commercial use without permission. Not downloaded in this pass. |
| [ComplexVAD / MERL on Zenodo](https://zenodo.org/records/15707073) | Public archives and explicit CC-BY-SA-4.0. Test set has 113 videos, 118 events, 40 types, per-frame event boxes and track numbers. Test+annotations archive is 22.1GB. | Promising broader behavior/localization benchmark with stronger annotation detail. Specific climbing coverage not established in this pass; no bulk download yet. |

Kaggle declares CC0 for its derivative, which is not independent verification of
rights in the underlying UCF footage. Counts above describe inspected versions,
not independent recordings. Vendor claims such as 95% annotation accuracy or 20%
detection improvement were not validated and are not adopted as project results.

## Other Mobius collections inspected

- [Fighting and Brawling](https://www.mobiusi.com/datasets/81f51dca5a8ccd8fa1f76981796bda1b): advertised MP4/JSON, 500 records / 1.7GB, free sample and contracted full access.
- [Suspicious Loitering](https://www.mobiusi.com/datasets/359905e01cdb4a4c4b5236ce0b2d5c99): advertised MP4/JSON, 500 records / 1.6GB. The prose claims timestamps, but the displayed sample schema lists only file metadata. Temporal annotations still need inspection. Use observable duration/location, not a dataset title as evidence of intent.
- [Robbery and Coercion](https://www.mobiusi.com/datasets/6a906e3d9dafa9cacb2733580c1005b4): advertised MP4/JSON, 500 records / 1.7GB and commercial authorization. Any later mapping must describe observable actions rather than infer criminality.

No vendor was contacted, no account created and no dataset purchased. These
collections are candidates, not confirmed full-data evaluations.

## Executed Mobius sample diagnostic

The HF repository explicitly links to the same Mobius listing and contains the
same sample filename. Pinned revision:
`55d92dcb2108a596c2a1d8bfe35fb8860db0237e`.
Source file SHA-256:
`2b3e0f3b531ee685c7b26e8036cd729bfc74b775611edd402c83822d63ee823c`.
Downloaded size 8,682,998 bytes; probed duration 36.337007 seconds, 1080x1920,
30fps. The sample is a close portrait-format nighttime view of someone traversing
a fence, rather than a wide fixed campus-camera view. Contact-sheet inspection
confirmed visible fence interaction and raised legs/body over the barrier.

The existing production 3B model with `temporal-v3-bounded` completed replay and
returned five candidate events: climbing at 16–23.5s and 32–36s, plus access
interaction, object tampering and another observable-event label. These extra
candidates require review. There are no publisher temporal labels in the sample
JSON, so no precision/recall or event-count accuracy is claimed. This establishes
that the current pipeline can emit climbing on a clear close view; it does not
explain all earlier misses or validate camera-scale performance.

Host artifacts: `datasets/mobius-sample/manifest.json`,
`datasets/mobius-sample/mobius-01.mp4`, `runs/mobius-predictions.json`.
The manifest explicitly marks labels unreviewed. The sample is in the review UI
as `mobius-01.mp4`. Raw media and metadata remain ignored by Git.

## Priority

Use the downloaded Mobius sample for the immediate input/recognition diagnostic.
For a larger video benchmark, inspect NWPU's permissions and annotation archive,
and ComplexVAD's relevant event categories. Use the image datasets only for a
separate localization/classification experiment. Do not substitute image counts
for numbers of independent videos or mix derivative UCF sources across splits.
