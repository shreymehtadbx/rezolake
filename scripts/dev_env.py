#!/usr/bin/env python3
"""Write a local-dev .env for `npm run dev` against a bundle target (default: dev).

    python3 scripts/dev_env.py -t <target>

Deploy the target first (scripts/bootstrap.py): the app's service principal must own the
Lakebase schema before you run the app locally with your own credentials.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-t", "--target", required=True)
    args = ap.parse_args()
    d = json.loads(subprocess.run(["databricks", "bundle", "validate", "-t", args.target, "-o", "json"],
                                  capture_output=True, text=True, cwd=ROOT, check=True).stdout)
    v = {k: x.get("value") for k, x in d["variables"].items()}
    profile = d["workspace"]["profile"]

    def cli(*a: str) -> dict:
        out = subprocess.run(["databricks", *a, "--profile", profile, "-o", "json"],
                             capture_output=True, text=True, check=True).stdout
        return json.loads(out)

    host = cli("postgres", "get-endpoint", v["lakebase_endpoint"])["status"]["hosts"]["host"]
    summary = json.loads(subprocess.run(["databricks", "bundle", "summary", "-t", args.target, "-o", "json"],
                                        capture_output=True, text=True, cwd=ROOT, check=True).stdout)
    job_id = summary["resources"]["jobs"]["resolve"]["id"]
    me = cli("current-user", "me")["userName"]
    env = {
        "DATABRICKS_CONFIG_PROFILE": profile,
        "DATABRICKS_WAREHOUSE_ID": v["sql_warehouse_id"],
        "DATABRICKS_JOB_ID": job_id,
        "LAKEBASE_ENDPOINT": v["lakebase_endpoint"],
        "PGHOST": host,
        "PGPORT": "5432",
        "PGDATABASE": v["pg_database"],
        "PGSSLMODE": "require",
        "DATABRICKS_APP_PORT": "8000",
        "DATABRICKS_APP_NAME": d["resources"]["apps"]["app"]["name"],
        "REZOLAKE_DEV_USER": me,
    }
    (ROOT / ".env").write_text("".join(f"{k}={val}\n" for k, val in env.items()))
    print(f"wrote .env for target {args.target} ({profile}); now: npm run dev")


if __name__ == "__main__":
    main()
