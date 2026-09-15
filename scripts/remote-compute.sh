#!/usr/bin/env bash
# Run a project Python command on the primary compute host, without syncing data.
set -euo pipefail
if [[ $# -eq 0 ]]; then
  echo "Usage: $0 scripts/compute-check.py [args...]" >&2
  exit 2
fi
# Quote arguments for the remote shell rather than interpolating command text.
printf -v command '%q ' /home/software/vesta/.venv/bin/python "$@"
exec ssh software@100.64.0.7 "cd /home/software/vesta && $command"
