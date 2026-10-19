#!/bin/sh
# Container entrypoint: seed the Volume once, start the weekly scheduler, run Streamlit.
set -e
DATA="${PULSE_DATA_DIR:-/data}"
mkdir -p "$DATA/runs"
# First boot: the Volume is empty, so copy the bundled (PII-scrubbed) runs. Never overwrites.
if [ -z "$(ls -A "$DATA/runs" 2>/dev/null)" ] && [ -d /app/deploy_data/runs ]; then
  echo "Seeding $DATA from deploy_data/"
  cp -r /app/deploy_data/runs/. "$DATA/runs/"
fi
if [ "${PULSE_SCHEDULE_ENABLED:-0}" = "1" ]; then
  python -m pulse.scheduler &
fi
exec streamlit run streamlit_app.py --server.port "${PORT:-8501}" --server.address 0.0.0.0 --server.headless true
