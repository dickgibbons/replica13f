#!/usr/bin/env bash
# Run Streamlit on the VPS hub port (8502). From repo root:
#   chmod +x scripts/serve-vps.sh && ./scripts/serve-vps.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PORT="${REPLICA13F_PORT:-8502}"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi
# shellcheck source=/dev/null
source .venv/bin/activate
exec streamlit run app.py --server.port="$PORT" --server.address=0.0.0.0
