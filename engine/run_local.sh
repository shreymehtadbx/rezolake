#!/bin/bash
# Run the engine locally (Databricks Connect, serverless) against a bundle target's environment.
# usage: engine/run_local.sh -t TARGET MODE [ENTITY_TYPE_ID] [extra --args]
set -euo pipefail
if [ "${1:-}" != "-t" ] || [ -z "${2:-}" ]; then echo "usage: $0 -t TARGET MODE [ENTITY_TYPE_ID]" >&2; exit 2; fi
TARGET=$2; shift 2
cd "$(dirname "$0")/.."
eval "$(databricks bundle validate -t "$TARGET" -o json | python3 -c '
import json, sys, shlex
d = json.load(sys.stdin); v = {k: x.get("value") for k, x in d["variables"].items()}
print(f"PROFILE={shlex.quote(d[\"workspace\"][\"profile\"])}")
for k in ("catalog", "lakebase_endpoint", "pg_database", "vs_endpoint"):
    print(f"{k.upper()}={shlex.quote(str(v[k]))}")')"
MODE=$1; ET=${2:-}; shift; shift || true
export REZOLAKE_LOCAL=1 DATABRICKS_CONFIG_PROFILE=$PROFILE MLFLOW_DISABLE_AGENT_HINT=1
exec engine/.venv/bin/python -c "from rezolake_engine.main import main; main()" \
  --mode "$MODE" --entity_type_id "$ET" --catalog "$CATALOG" --lakebase_endpoint "$LAKEBASE_ENDPOINT" \
  --pg_database "$PG_DATABASE" --vs_endpoint "$VS_ENDPOINT" --trigger_source local --requested_by local "$@"
