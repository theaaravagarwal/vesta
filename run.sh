#!/usr/bin/env bash
set -euo pipefail
PORT="${PORT:-33263}"
echo "Starting Vesta review at http://127.0.0.1:${PORT}/review"
exec uv run --frozen --extra host gunicorn --bind "127.0.0.1:${PORT}" --workers 1 --threads 8 --timeout 360 'behavior:create_app()'
