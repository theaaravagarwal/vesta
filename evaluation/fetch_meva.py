"""Fetch three small, attributed official MEVA clips for exploratory replay only.

No labels are inferred from file names or model predictions. Run on compute host:
  .venv/bin/python evaluation/fetch_meva.py
"""

import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

BASE = "https://mevadata-public-01.s3.amazonaws.com/"
KEYS = [
    "drops-123-r13/2018-03-05/09/2018-03-05.09-49-41.09-50-01.school.G339.r13.avi",
    "drops-123-r13/2018-03-05/09/2018-03-05.09-49-44.09-50-00.school.G424.r13.avi",
    "drops-123-r13/2018-03-05/09/2018-03-05.09-49-46.09-50-00.school.G419.r13.avi",
]
ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "datasets" / "meva-demo"
LIMIT = 64 * 1024 * 1024


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    clips = []
    for key in KEYS:
        dest = DEST / Path(key).name
        if not dest.exists():
            temp = dest.with_suffix(".part")
            try:
                size = 0
                with (
                    urlopen(BASE + key, timeout=60) as response,
                    temp.open("wb") as output,
                ):
                    while block := response.read(1024 * 1024):
                        size += len(block)
                        if size > LIMIT:
                            raise ValueError("Refusing unexpectedly large sample")
                        output.write(block)
                temp.replace(dest)
            finally:
                temp.unlink(missing_ok=True)
        digest = hashlib.sha256(dest.read_bytes()).hexdigest()
        clips.append(
            {
                "id": dest.stem,
                "path": str(dest.relative_to(ROOT)),
                "source_url": BASE + key,
                "license": "CC-BY-4.0",
                "attribution": "MEVA dataset, Kitware Inc. and IARPA; Corona et al., WACV 2021",
                "session_group": "MEVA-2018-03-05-09",
                "split": "exploratory",
                "sha256": digest,
                "label_status": "unreviewed",
                "events": [],
            }
        )
        print(f"Available: {dest.name} sha256={digest}")
    manifest = {
        "schema_version": 1,
        "purpose": "Exploratory real-video replay; not a labeled accuracy benchmark",
        "license_url": "https://mevadata.org/resources/MEVA-data-license.txt",
        "clips": clips,
    }
    path = DEST / "manifest.json"
    if path.exists():
        existing = json.loads(path.read_text())
        if any(c.get("label_status") == "reviewed" for c in existing.get("clips", [])):
            print("Preserving existing reviewed manifest")
            return
    path.write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
