#!/usr/bin/env python3
"""End-to-end smoke test of a deployed environment through the app's API.

    python3 scripts/smoke_test.py -t <target>            # needs `bootstrap.py --with-fixtures` first

Creates (or reuses) the `customer` entity type over the rezolake2_fixtures sources, publishes it,
runs FULL resolution on the engine job, then scores the entities against the fixture truth
(pairwise precision / recall). Exits non-zero if anything fails.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ATTRS = [
    ("a_first_name", "first_name", "First name", "name", "SOURCE_PRIORITY", "title"),
    ("a_last_name", "last_name", "Last name", "name", "SOURCE_PRIORITY", "title"),
    ("a_email", "email", "Email", "email", "MOST_COMMON", "lower"),
    ("a_phone", "phone", "Phone", "phone", "MOST_RECENT_NON_NULL", "none"),
    ("a_city", "city", "City", "address", "MOST_RECENT_NON_NULL", "title"),
]


def definition(catalog: str) -> dict:
    fx = f"{catalog}.rezolake2_fixtures"
    return {
        "attributes": [
            {"attribute_id": aid, "name": n, "display_name": dn, "data_type": "string", "semantic_type": st,
             "matching": True, "display": True, "survivorship": {"rule": rule}, "standardize": std}
            for aid, n, dn, st, rule, std in ATTRS
        ],
        "sources": [
            {"source_id": "crm", "name": "CRM contacts", "table": f"{fx}.crm_contacts", "primary_key": ["contact_id"],
             "priority": 10, "active": True,
             "mappings": {"first_name": "a_first_name", "last_name": "a_last_name", "email": "a_email",
                          "phone": "a_phone", "city": "a_city", "segment": "__extension__"}},
            {"source_id": "erp", "name": "ERP customers", "table": f"{fx}.erp_customers", "primary_key": ["cust_no"],
             "priority": 20, "active": True,
             "mappings": {"given_name": "a_first_name", "family_name": "a_last_name", "email_address": "a_email",
                          "tel": "a_phone", "city": "a_city"}},
            {"source_id": "web", "name": "Web signups", "table": f"{fx}.web_signups", "primary_key": ["user_id"],
             "priority": 30, "active": True,
             "mappings": {"fname": "a_first_name", "lname": "a_last_name", "email": "a_email",
                          "mobile": "a_phone", "city": "a_city"}},
        ],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-t", "--target", required=True)
    ap.add_argument("--timeout", type=int, default=2400)
    args = ap.parse_args()

    d = json.loads(subprocess.run(["databricks", "bundle", "validate", "-t", args.target, "-o", "json"],
                                  capture_output=True, text=True, cwd=ROOT, check=True).stdout)
    profile, catalog, app_name = d["workspace"]["profile"], d["variables"]["catalog"]["value"], \
        d["resources"]["apps"]["app"]["name"]
    url = json.loads(subprocess.run(["databricks", "apps", "get", app_name, "--profile", profile, "-o", "json"],
                                    capture_output=True, text=True, check=True).stdout)["url"]

    def token() -> str:
        out = subprocess.run(["databricks", "auth", "token", "--profile", profile, "-o", "json"],
                             capture_output=True, text=True, check=True).stdout
        return json.loads(out)["access_token"]

    def call(method: str, path: str, body=None):
        req = urllib.request.Request(url + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {token()}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                text = r.read().decode()
                return json.loads(text) if text else {}
        except urllib.error.HTTPError as e:
            return {"_status": e.code, "error": e.read().decode()[:500]}

    def wait(run_id: str) -> dict:
        t0, last = time.time(), None
        while time.time() - t0 < args.timeout:
            r = call("GET", f"/api/runs/{run_id}")
            state = (r.get("status"), r.get("stage"))
            if state != last:
                print(f"  run {run_id[:8]}: {state[0]} {state[1] or ''}", flush=True)
                last = state
            if r.get("status") in ("SUCCEEDED", "FAILED", "CANCELLED"):
                return r
            time.sleep(15)
        sys.exit("timed out waiting for run")

    print(f"app {url} catalog {catalog}")
    created = call("POST", "/api/entity-types",
                   {"name": "Customer", "entity_kind": "people", "description": "Smoke-test customers"})
    print("entity type:", "created" if "id" in created else f"reused ({created.get('_status')})")
    r = call("PUT", "/api/entity-types/customer/definition", definition(catalog))
    assert r.get("ok"), r
    pub = wait(call("POST", "/api/entity-types/customer/publish")["run_id"])
    assert pub["status"] == "SUCCEEDED", pub
    run = wait(call("POST", "/api/entity-types/customer/runs", {"mode": "FULL"})["run_id"])
    assert run["status"] == "SUCCEEDED", run
    m = run.get("metrics", {})
    print("metrics:", {k: m.get(k) for k in ("work_set", "candidate_pairs", "pairs_scored", "decisions_link",
                                            "decisions_review", "entities")})

    ev = subprocess.run([sys.executable, str(ROOT / "scripts" / "sql.py"), "-t", args.target,
                         str(ROOT / "test_data" / "evaluate_customers.sql")], capture_output=True, text=True)
    if ev.returncode != 0:
        sys.exit(ev.stderr)
    res = json.loads(ev.stdout)[0]
    print("evaluation:", res)
    if float(res["recall"]) < 0.9:
        sys.exit("recall below 0.9")
    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    main()
