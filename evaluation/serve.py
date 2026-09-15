"""Isolated loopback experiment app, launched with Gunicorn by an operator.

VESTA_EXPERIMENT_RUNTIME must be set to a fresh ignored directory. The normal
review database is never selected implicitly. Use a single worker and the same
GPU dependency environment as production, without concurrent analysis jobs.
"""
import os
from pathlib import Path
os.environ.setdefault("BEHAVIOR_EVENT_POLICY", "observable-v3")
from behavior import create_app as behavior_app


def create_app():
    runtime = Path(os.environ["VESTA_EXPERIMENT_RUNTIME"]).resolve()
    production = (Path(__file__).resolve().parents[1] / "runtime" / "behavior").resolve()
    if runtime == production:
        raise ValueError("Experiment runtime must differ from production")
    return behavior_app({"BEHAVIOR_RUNTIME": runtime})
