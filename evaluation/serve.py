"""Isolated loopback experiment app, launched with Gunicorn by an operator.

VESTA_EXPERIMENT_RUNTIME must be set to a fresh ignored directory. The normal
review database is never selected implicitly. Use a single worker and the same
GPU dependency environment as production, without concurrent analysis jobs.

``BEHAVIOR_EVENT_POLICY`` and ``BEHAVIOR_FOCUS_VIEW`` must both be set
explicitly: prompt policy and spatial crops are separate variables, and an
experiment must never inherit a default that its artifacts do not record.
"""
import logging
import os
from pathlib import Path

REQUIRED_VARIANT = ("BEHAVIOR_EVENT_POLICY", "BEHAVIOR_FOCUS_VIEW")
_missing = [name for name in REQUIRED_VARIANT if not os.environ.get(name)]
if _missing:
    raise ValueError(
        "Experiment runs must select a variant explicitly: " + ", ".join(_missing)
    )

from behavior import config_version, create_app as behavior_app


def create_app():
    runtime = Path(os.environ["VESTA_EXPERIMENT_RUNTIME"]).resolve()
    production = (Path(__file__).resolve().parents[1] / "runtime" / "behavior").resolve()
    if runtime == production:
        raise ValueError("Experiment runtime must differ from production")
    logging.getLogger(__name__).warning(
        "Experiment runtime %s selected variant %s", runtime, config_version()
    )
    return behavior_app({"BEHAVIOR_RUNTIME": runtime})
