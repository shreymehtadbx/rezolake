#!/usr/bin/env python3
"""Initialize (or re-sync) a Rezolake environment from a bundle target.

    python3 scripts/bootstrap.py -t <target> [--with-fixtures] [--with-ai-search] [--skip-deploy]

Idempotent: safe to re-run. Steps
  1. resolve the target's settings from databricks.yml (bundle validate)
  2. check tooling, auth, catalog, SQL warehouse, model serving endpoints (and warn if AI Decide is off)
  3. create the Lakebase project if missing (and the AI Search endpoint, with --with-ai-search)
  4. deploy the bundle (engine job + wheel + app) and start the app
  5. run the engine SETUP mode (Delta contracts + grants for the app service principal)
  6. optionally load the 3-source customer test fixture
Needs only the Databricks CLI (authenticated profile), uv, node/npm and python3 on PATH.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NPMRC_TEMPLATE = ROOT / "scripts" / "templates" / "npmrc.public"
EXIT_APP_PACKAGES = 3  # the app build could not install npm packages (setup.py retries with the public registry)
REQUIRED_ENDPOINTS = ["databricks-bge-large-en", "databricks-claude-sonnet-4-6"]  # engine strategy defaults
AI_DECIDE_PROBE = {"state": "probe", "questions": {"ok": {"type": "noul", "instructions": "Is this a probe?"}}}


def log(msg: str) -> None:
    print(f"[bootstrap] {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"[bootstrap] ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


class Cli:
    def __init__(self, profile: str, target: str):
        self.profile, self.target = profile, target

    def run(self, *args: str, check: bool = True, json_out: bool = True, timeout: int = 1800):
        cmd = ["databricks", *args, "--profile", self.profile]
        if json_out:
            cmd += ["-o", "json"]
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, timeout=timeout)
        if check and r.returncode != 0:
            fail(f"`{' '.join(cmd)}` failed:\n{(r.stderr or r.stdout).strip()[:2000]}")
        if not json_out:
            return r
        try:
            return json.loads(r.stdout) if r.stdout.strip() else {}
        except json.JSONDecodeError:
            return {"_raw": r.stdout, "_rc": r.returncode, "_err": r.stderr}

    def ok(self, *args: str) -> bool:
        return self.run(*args, check=False, json_out=False).returncode == 0

    def bundle(self, *args: str, timeout: int = 1800) -> None:
        cmd = ["databricks", "bundle", *args, "-t", self.target]
        log("$ " + " ".join(cmd))
        r = subprocess.run(cmd, cwd=ROOT, timeout=timeout)
        if r.returncode != 0:
            fail(f"bundle {' '.join(args)} failed")

    def sql(self, warehouse: str, statement: str) -> None:
        r = self.run("experimental", "aitools", "tools", "query", "--warehouse", warehouse, statement, check=False)
        if isinstance(r, dict) and r.get("_rc"):
            fail(f"SQL failed: {statement[:120]}...\n{r.get('_err', '')[:1500]}")


def resolve_target(target: str) -> dict:
    r = subprocess.run(["databricks", "bundle", "validate", "-t", target, "-o", "json"],
                       capture_output=True, text=True, cwd=ROOT)
    if r.returncode != 0:
        fail(f"bundle validate -t {target} failed:\n{r.stderr[:2000]}")
    d = json.loads(r.stdout)
    v = {k: x.get("value") for k, x in d["variables"].items()}
    return {"profile": d["workspace"].get("profile"), "host": d["workspace"]["host"], "vars": v,
            "app_name": d["resources"]["apps"]["app"]["name"],
            "public_npm": ".npmrc" in (d.get("sync") or {}).get("include", [])}


def ensure_npmrc(public_npm: bool) -> None:
    """Targets that sync .npmrc build the app from the public npm registry; the file itself is not in git."""
    if not public_npm:
        return
    npmrc = ROOT / ".npmrc"
    if not npmrc.exists() or npmrc.read_text() != NPMRC_TEMPLATE.read_text():
        npmrc.write_text(NPMRC_TEMPLATE.read_text())
        log("wrote .npmrc (this target builds the app from the public npm registry)")


def app_package_errors(cli: "Cli", app_name: str) -> list[str]:
    """npm errors from the app's latest build log (empty if the failure was something else)."""
    r = subprocess.run(["databricks", "apps", "logs", app_name, "--tail-lines", "300", "--profile", cli.profile],
                       capture_output=True, text=True, timeout=120)
    lines = [ln for ln in r.stdout.splitlines() if "npm error 404" in ln or "seems to be corrupted" in ln
             or "Error installing packages" in ln]
    return lines[-6:]


def check_tools() -> None:
    for tool in ("databricks", "uv", "npm", "node"):
        if not shutil.which(tool):
            fail(f"`{tool}` not found on PATH")
    ver = subprocess.run(["databricks", "--version"], capture_output=True, text=True).stdout.strip()
    log(f"tools ok ({ver})")


def ensure_lakebase(cli: Cli, project: str, endpoint: str) -> None:
    pid = project.split("/", 1)[1]
    if cli.ok("postgres", "get-project", project):
        log(f"Lakebase project {project} exists")
    else:
        log(f"creating Lakebase project {project} (waits for completion)...")
        cli.run("postgres", "create-project", pid, "--json",
                json.dumps({"spec": {"display_name": "Rezolake"}}), timeout=1800)
    branch = endpoint.rsplit("/endpoints/", 1)[0]
    eps = cli.run("postgres", "list-endpoints", branch)
    names = [e["name"] for e in (eps if isinstance(eps, list) else eps.get("endpoints", []))]
    if endpoint not in names:
        fail(f"expected Lakebase endpoint {endpoint}, found {names}; set lakebase_endpoint for this target")
    log(f"Lakebase endpoint {endpoint} ok")


def ensure_vs_endpoint(cli: Cli, name: str) -> None:
    got = cli.run("vector-search-endpoints", "get-endpoint", name, check=False)
    if isinstance(got, dict) and got.get("name") == name:
        log(f"AI Search endpoint {name}: {got.get('endpoint_status', {}).get('state')}")
        return
    log(f"creating AI Search endpoint {name} (STANDARD; provisioning continues in the background)...")
    cli.run("vector-search-endpoints", "create-endpoint", name, "STANDARD", "--no-wait")


def wait_vs_online(cli: Cli, name: str, timeout_s: int = 1800) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        st = cli.run("vector-search-endpoints", "get-endpoint", name).get("endpoint_status", {}).get("state")
        if st == "ONLINE":
            log(f"AI Search endpoint {name} ONLINE")
            return
        log(f"AI Search endpoint {name}: {st} (waiting)")
        time.sleep(30)
    fail(f"AI Search endpoint {name} not ONLINE after {timeout_s}s")


def check_ai_decide(cli: Cli) -> None:
    r = cli.run("api", "post", "/api/2.0/ai-functions/ai-decide", "--json", json.dumps(AI_DECIDE_PROBE), check=False)
    if isinstance(r, dict) and r.get("response"):
        log("AI Decide ok")
        return
    err = ((r.get("_err") or r.get("_raw") or "") if isinstance(r, dict) else str(r)).strip()
    log("WARNING: AI Decide is unavailable, so strategies using the ai_decide matcher will fail (the default "
        "LLM matcher is unaffected). A workspace admin can enable the AI Decide preview in Settings > Previews."
        + (f" Details: {err[:300]}" if err else ""))


def load_fixtures(cli: Cli, catalog: str, warehouse: str, app_sp: str) -> None:
    sql = subprocess.run([sys.executable, str(ROOT / "test_data" / "generate_customers.py"), catalog],
                         capture_output=True, text=True, check=True).stdout
    statements = [s.strip() for s in sql.split(";\n") if s.strip()]
    log(f"loading customer fixture into {catalog}.rezolake2_fixtures ({len(statements)} statements)")
    for s in statements:
        cli.sql(warehouse, s.rstrip(";"))
    # The app SP owns the table-update trigger jobs; they need to read the watched sources.
    cli.sql(warehouse, f"GRANT USE SCHEMA, SELECT ON SCHEMA `{catalog}`.rezolake2_fixtures TO `{app_sp}`")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-t", "--target", required=True)
    ap.add_argument("--with-fixtures", action="store_true", help="load the 3-source customer test fixture")
    ap.add_argument("--with-ai-search", action="store_true",
                    help="also create the AI Search endpoint (only needed for strategies using ai_search_blocker)")
    ap.add_argument("--skip-deploy", action="store_true", help="only (re)run checks, SETUP and fixtures")
    args = ap.parse_args()

    check_tools()
    cfg = resolve_target(args.target)
    v = cfg["vars"]
    if not cfg["profile"]:
        fail(f"target {args.target} has no workspace.profile")
    cli = Cli(cfg["profile"], args.target)
    log(f"target={args.target} host={cfg['host']} profile={cfg['profile']} catalog={v['catalog']}")

    me = cli.run("current-user", "me")
    log(f"authenticated as {me.get('userName')}")

    if not cli.ok("catalogs", "get", v["catalog"]):
        fail(f"catalog {v['catalog']} not found or not visible; create it (or pick one you can CREATE SCHEMA in)")
    log(f"catalog {v['catalog']} ok")

    wh = cli.run("warehouses", "get", v["sql_warehouse_id"], check=False)
    if not isinstance(wh, dict) or not wh.get("id"):
        fail(f"SQL warehouse {v['sql_warehouse_id']} not found")
    log(f"warehouse {wh['name']} ({wh.get('warehouse_type')}) state={wh.get('state')}")
    if wh.get("state") != "RUNNING":
        cli.run("warehouses", "start", v["sql_warehouse_id"], "--no-wait", check=False)
    write_wh = v["sql_warehouse_id"]
    if wh.get("warehouse_type") == "REALTIME":
        log("note: Lakehouse//RT is read-only; fixture loading needs a regular warehouse (--with-fixtures will pick one)")
        others = [w for w in cli.run("warehouses", "list") if w.get("warehouse_type") != "REALTIME"]
        write_wh = others[0]["id"] if others else ""

    names = {e["name"] for e in cli.run("serving-endpoints", "list")}
    missing = [e for e in REQUIRED_ENDPOINTS if e not in names]
    if missing:
        fail(f"serving endpoints missing: {missing} (edit the strategy defaults or enable them)")
    log(f"serving endpoints ok: {REQUIRED_ENDPOINTS}")

    check_ai_decide(cli)
    ensure_lakebase(cli, v["lakebase_project"], v["lakebase_endpoint"])
    if args.with_ai_search:
        ensure_vs_endpoint(cli, v["vs_endpoint"])

    ensure_npmrc(cfg["public_npm"])
    if not args.skip_deploy:
        cli.bundle("deploy")
        log(f"$ databricks bundle run app -t {args.target}")
        if subprocess.run(["databricks", "bundle", "run", "app", "-t", args.target], cwd=ROOT, timeout=1800).returncode:
            errors = app_package_errors(cli, cfg["app_name"])
            if errors and not cfg["public_npm"]:
                print("[bootstrap] ERROR: the app build could not install npm packages from this workspace's npm proxy:\n  "
                      + "\n  ".join(errors) + "\n[bootstrap] Fix: build from the public npm registry for this target "
                      "(`python3 scripts/setup.py --profile <profile> --npm-registry public`, or see SETUP.md).",
                      file=sys.stderr)
                sys.exit(EXIT_APP_PACKAGES)
            fail("bundle run app failed" + (":\n  " + "\n  ".join(errors) if errors else ""))

    app = cli.run("apps", "get", cfg["app_name"])
    app_sp = app.get("service_principal_client_id")
    log(f"app {cfg['app_name']} url={app.get('url')} sp={app_sp} state={app.get('app_status', {}).get('state')}")

    summary = subprocess.run(["databricks", "bundle", "summary", "-t", args.target, "-o", "json"],
                             capture_output=True, text=True, cwd=ROOT)
    job_id = json.loads(summary.stdout)["resources"]["jobs"]["resolve"]["id"]
    log(f"running engine SETUP on job {job_id} (serverless start-up takes a few minutes)...")
    run = cli.run("jobs", "run-now", "--json", json.dumps(
        {"job_id": int(job_id), "job_parameters": {"mode": "SETUP", "app_sp": app_sp}}), timeout=3600)
    state = run.get("state", {})
    if state.get("result_state") != "SUCCESS":
        fail(f"SETUP run did not succeed: {state}")
    log("SETUP succeeded: rezolake2_* schemas, tables, views and app grants in place")

    if args.with_fixtures:
        if not write_wh:
            fail("no non-RT warehouse available to load fixtures")
        load_fixtures(cli, v["catalog"], write_wh, app_sp)

    if args.with_ai_search:
        wait_vs_online(cli, v["vs_endpoint"])
    log("done.")
    log(f"open {app.get('url')} , create an entity type, add sources, publish, and run.")


if __name__ == "__main__":
    main()
