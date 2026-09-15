"""Export reviewed corrections as annotation candidates, never training labels.

Run on the host: .venv/bin/python evaluation/export_reviews.py --output runs/reviews.json
"""

import argparse
import json
from pathlib import Path
from urllib.request import urlopen

p = argparse.ArgumentParser()
p.add_argument("--base-url", default="http://127.0.0.1:33263")
p.add_argument("--output", required=True, type=Path)
a = p.parse_args()


def get(path):
    with urlopen(a.base_url.rstrip("/") + path, timeout=30) as response:
        return json.load(response)


rows = []
for video in get("/api/videos")["videos"]:
    detail = get("/api/videos/" + video["id"])
    for event in detail["events"]:
        if event["review_status"] != "unreviewed" or event["correction"]:
            rows.append(
                {
                    "video": video,
                    "event": event,
                    "label_status": "requires_adjudication",
                }
            )
a.output.parent.mkdir(parents=True, exist_ok=True)
a.output.write_text(
    json.dumps(
        {
            "schema_version": 1,
            "purpose": "Reviewed annotation candidates; no automatic model promotion",
            "items": rows,
        },
        indent=2,
    )
    + "\n"
)
print(f"Exported {len(rows)} annotation candidates")
