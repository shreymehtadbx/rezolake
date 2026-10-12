#!/usr/bin/env python3
"""Set up Rezolake in a new Databricks workspace with one command.

    python3 scripts/setup.py --profile <cli-profile> [--target <name>] [--catalog <catalog>]
                             [--warehouse <id>] [--lakebase-project projects/<id>] [--app-name <name>]
                             [--npm-registry auto|proxy|public] [--with-fixtures] [--with-ai-search] [--yes]

1. checks tools and CLI auth for the profile;
2. picks the catalog, SQL warehouse and Lakebase project (flags win; otherwise it inspects the workspace,
   proposes a choice and asks — or takes the proposal with --yes);
3. writes the bundle target targets/<target>.yml (DABs; databricks.yml includes targets/*.yml) and validates it;
4. runs scripts/bootstrap.py for the target (Lakebase project, bundle deploy, app start, engine SETUP,
   optional test fixtures). If the app build cannot install npm packages from the workspace's npm proxy
   (--npm-registry auto, the default), it switches the target to the public npm registry and retries once;
5. with --with-fixtures, runs scripts/smoke_test.py (entity type over the fixtures, FULL run, evaluation).

Re-running is safe: an existing target file is reused unless you pass --rewrite.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIN_CLI = (1, 17)
EXIT_APP_PACKAGES = 3  # bootstrap.py: the app build could not install npm packages
NPM_SYNC_BLOCK = """    # The app build fetches npm packages from the public registry (.npmrc; see SETUP.md, "npm registry").
    sync:
      include:
        - ../.npmrc
"""


def log(msg: str) -> None:
    print(f"[setup] {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"[setup] ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def cli(profile: str, *args: str, check: bool = True):
    r = subprocess.run(["databricks", *args, "--profile", profile, "-o", "json"], capture_output=True, text=True)
    if r.returncode != 0:
        if check:
            fail(f"`databricks {' '.join(args)}` failed: {(r.stderr or r.stdout).strip()[:800]}")
        return None
    out = r.stdout.strip()
    return json.loads(out) if out else {}


def as_list(d, key: str) -> list:
    return d if isinstance(d, list) else (d or {}).get(key, [])


def choose(what: str, options: list[tuple[str, str]], proposed: str | None, assume_yes: bool) -> str:
    """options: (value, description). Returns the chosen value."""
    if not options:
        fail(f"no usable {what} found in this workspace (see SETUP.md prerequisites)")
    if proposed is None:
        proposed = options[0][0]
    if assume_yes or len(options) == 1:
        log(f"{what}: {proposed}")
        return proposed
    print(f"\nChoose the {what}:")
    for i, (value, desc) in enumerate(options, 1):
        print(f"  {i}. {value}  {desc}{'   <- proposed' if value == proposed else ''}")
    answer = input(f"Number or name [{proposed}]: ").strip()
    if not answer:
        return proposed
    if answer.isdigit() and 1 <= int(answer) <= len(options):
        return options[int(answer) - 1][0]
    return answer


def check_tools() -> None:
    for tool in ("databricks", "uv", "node", "npm"):
        if not shutil.which(tool):
            fail(f"`{tool}` not found on PATH (see SETUP.md prerequisites)")
    ver = subprocess.run(["databricks", "--version"], capture_output=True, text=True).stdout
    m = re.search(r"(\d+)\.(\d+)", ver)
    if not m or (int(m.group(1)), int(m.group(2))) < MIN_CLI:
        fail(f"Databricks CLI {'.'.join(map(str, MIN_CLI))}+ required, found: {ver.strip()}")
    log(f"tools ok ({ver.strip()})")


def profile_host(profile: str) -> str:
    out = subprocess.run(["databricks", "auth", "profiles", "-o", "json"], capture_output=True, text=True).stdout
    for p in json.loads(out or "{}").get("profiles", []):
        if p.get("name") == profile:
            if not p.get("valid", True):
                fail(f"profile {profile} is not authenticated: run `databricks auth login --profile {profile}`")
            return p["host"].rstrip("/")
    fail(f"profile {profile} not found (`databricks auth profiles` lists yours). To create one, run "
         f"`databricks auth login --host <workspace-url>` and enter a profile name when prompted")
    return ""


def writable_catalogs(profile: str, user: str, groups: set[str]) -> list[tuple[str, str]]:
    out = []
    for c in as_list(cli(profile, "catalogs", "list"), "catalogs"):
        if c.get("catalog_type") != "MANAGED_CATALOG" or c["name"] in ("system", "samples"):
            continue
        owner = c.get("owner", "")
        # Workspace catalogs are owned by a `_workspace_admins_<workspace>` group that workspace admins belong to.
        ok = owner == user or owner in groups or ("admins" in groups and owner.startswith("_workspace_admins_"))
        if not ok:
            eff = cli(profile, "grants", "get-effective", "catalog", c["name"], "--principal", user, check=False) or {}
            privs = {p.get("privilege") for a in eff.get("privilege_assignments", []) for p in a.get("privileges", [])}
            ok = bool(privs & {"CREATE_SCHEMA", "ALL_PRIVILEGES"}) and "USE_CATALOG" in privs | {"ALL_PRIVILEGES"}
        if ok:
            out.append((c["name"], f"(owner {owner})"))
    return out


def use_public_npm(path: Path) -> bool:
    """Make the target build its app from the public npm registry. Returns False if it already does."""
    text = path.read_text()
    if "../.npmrc" in text:
        return False
    path.write_text(text.rstrip("\n") + "\n" + NPM_SYNC_BLOCK)
    log(f"{path.relative_to(ROOT)}: app build now uses the public npm registry")
    return True


def write_target(path: Path, target: str, host: str, profile: str, catalog: str, warehouse: str,
                 lakebase_project: str, app_name: str) -> None:
    lines = [
        f"# Rezolake environment `{target}` (written by scripts/setup.py; edit freely).",
        "targets:",
        f"  {target}:",
        "    workspace:",
        f"      host: {host}",
        f"      profile: {profile}",
        "    variables:",
        f"      catalog: {catalog}",
        f"      sql_warehouse_id: \"{warehouse}\"",
        f"      lakebase_project: {lakebase_project}",
    ]
    if app_name != "rezolake":
        lines.append(f"      app_name: {app_name}")
    path.write_text("\n".join(lines) + "\n")
    log(f"wrote {path.relative_to(ROOT)}")


def run(cmd: list[str]) -> None:
    log("$ " + " ".join(cmd))
    if subprocess.run(cmd, cwd=ROOT).returncode != 0:
        fail(f"`{' '.join(cmd)}` failed (see output above)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", required=True, help="Databricks CLI profile of the target workspace")
    ap.add_argument("--target", help="bundle target name (default: derived from the profile)")
    ap.add_argument("--catalog", help="Unity Catalog catalog for the rezolake2_* schemas (needs CREATE SCHEMA)")
    ap.add_argument("--warehouse", help="SQL warehouse id the app reads through")
    ap.add_argument("--lakebase-project", default="projects/rezolake", help="Lakebase project (created if missing)")
    ap.add_argument("--app-name", default="rezolake", help="Databricks App name")
    ap.add_argument("--npm-registry", choices=("auto", "proxy", "public"), default="auto",
                    help="where the app build gets npm packages: the workspace's Databricks npm proxy, the public "
                         "registry, or auto (proxy; switch to public and retry if the proxy is missing packages)")
    ap.add_argument("--with-fixtures", action="store_true", help="load the customer test fixture and run the smoke test")
    ap.add_argument("--with-ai-search", action="store_true", help="also create the AI Search endpoint (optional blocker)")
    ap.add_argument("--rewrite", action="store_true", help="rewrite targets/<target>.yml even if it exists")
    ap.add_argument("--yes", "-y", action="store_true", help="take the proposed choices without prompting")
    args = ap.parse_args()

    check_tools()
    host = profile_host(args.profile)
    me = cli(args.profile, "current-user", "me")
    user = me.get("userName")
    groups = {g.get("display") for g in me.get("groups", [])}
    log(f"workspace {host} as {user}{' (workspace admin)' if 'admins' in groups else ''}")

    target = args.target or re.sub(r"[^a-z0-9_-]", "-", args.profile.lower())
    if target == "prod":
        fail("use a different target name; `prod` is the maintainers' live environment")
    target_file = ROOT / "targets" / f"{target}.yml"

    if target_file.exists() and not args.rewrite:
        log(f"reusing existing {target_file.relative_to(ROOT)} (pass --rewrite to regenerate)")
    else:
        catalogs = writable_catalogs(args.profile, user, groups)
        catalog = args.catalog or choose("catalog", catalogs, None, args.yes)
        if args.catalog and args.catalog not in {c for c, _ in catalogs}:
            log(f"note: could not confirm CREATE SCHEMA on {args.catalog}; bootstrap will fail clearly if missing")

        whs = cli(args.profile, "warehouses", "list") or []
        usable = [w for w in whs if w.get("enable_serverless_compute") or w.get("warehouse_type") == "REALTIME"]
        usable.sort(key=lambda w: (w.get("warehouse_type") != "REALTIME", w.get("state") != "RUNNING", w["name"]))
        options = [(w["id"], f"{w['name']} ({w.get('warehouse_type')}, {'serverless' if w.get('enable_serverless_compute') else 'classic'}, {w.get('state')})") for w in usable]
        warehouse = args.warehouse or choose("SQL warehouse (serverless or Lakehouse//RT)", options, None, args.yes)

        if not re.fullmatch(r"projects/[a-z0-9][a-z0-9-]{0,62}", args.lakebase_project):
            fail("--lakebase-project must look like projects/<lowercase-id>")
        write_target(target_file, target, host, args.profile, catalog, warehouse, args.lakebase_project, args.app_name)

    if args.npm_registry == "public":
        use_public_npm(target_file)

    r = subprocess.run(["databricks", "bundle", "validate", "-t", target], cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"bundle validate -t {target} failed:\n{(r.stderr or r.stdout)[-1500:]}")
    log(f"bundle target {target} validates")

    if not (ROOT / "node_modules").exists():
        run(["npm", "install"])

    boot = [sys.executable, "scripts/bootstrap.py", "-t", target]
    if args.with_fixtures:
        boot.append("--with-fixtures")
    if args.with_ai_search:
        boot.append("--with-ai-search")
    log("$ " + " ".join(boot))
    rc = subprocess.run(boot, cwd=ROOT).returncode
    if rc == EXIT_APP_PACKAGES and args.npm_registry == "auto" and use_public_npm(target_file):
        log("retrying with the public npm registry")
        rc = subprocess.run(boot, cwd=ROOT).returncode
    if rc:
        fail(f"`{' '.join(boot)}` failed (see output above)")

    if args.with_fixtures:
        run([sys.executable, "scripts/smoke_test.py", "-t", target])

    app = cli(args.profile, "apps", "get", args.app_name, check=False) or {}
    log("done.")
    log(f"app: {app.get('url', '(see Databricks Apps)')}")
    log(f"next: open the app, create an entity type over your source tables, publish, run. Redeploy with "
        f"`databricks bundle deploy -t {target} && databricks bundle run app -t {target}`.")


if __name__ == "__main__":
    main()
