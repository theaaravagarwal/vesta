"""Guards against documentation drift that review does not reliably catch.

These check structure, never wording: that project docs do not point at files
which have moved or were never added, and that every generated benchmark record
is reachable from the runs index. A record nobody links is a result nobody reads.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = sorted((ROOT / "docs" / "context").glob("*.md")) + [
    ROOT / "README.md",
    ROOT / "AGENTS.md",
    ROOT / "evaluation" / "README.md",
]
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


class DocumentationLinkTests(unittest.TestCase):
    def test_relative_links_resolve(self):
        missing = []
        for doc in DOCS:
            if not doc.is_file():
                continue
            for target in LINK.findall(doc.read_text()):
                if target.startswith(("http://", "https://", "#", "mailto:")):
                    continue
                path = (doc.parent / target.split("#", 1)[0]).resolve()
                if not path.exists():
                    missing.append(f"{doc.relative_to(ROOT)} -> {target}")
        self.assertEqual(missing, [], f"broken relative links: {missing}")

    def test_every_benchmark_record_is_linked_from_the_runs_index(self):
        index = (ROOT / "docs" / "context" / "public-benchmark.md").read_text()
        records = sorted((ROOT / "docs" / "context" / "benchmarks").glob("*.json"))
        self.assertTrue(records, "no benchmark records found")
        unlinked = [r.name for r in records if f"benchmarks/{r.name}" not in index]
        self.assertEqual(unlinked, [], f"unlinked benchmark records: {unlinked}")


if __name__ == "__main__":
    unittest.main()
