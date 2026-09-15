"""Score exported predictions; refuses to interpret unreviewed clips as negatives."""

import argparse
import json
from pathlib import Path
from metrics import score

p = argparse.ArgumentParser()
p.add_argument("manifest", type=Path)
p.add_argument("predictions", type=Path)
p.add_argument("--iou", type=float, default=0.3)
a = p.parse_args()
if not 0 < a.iou <= 1:
    p.error("--iou must be in (0,1]")
print(
    json.dumps(
        score(
            json.loads(a.manifest.read_text()),
            json.loads(a.predictions.read_text()),
            a.iou,
        ),
        indent=2,
    )
)
