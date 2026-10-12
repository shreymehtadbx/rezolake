#!/usr/bin/env python3
"""Run a SQL file against a bundle target, substituting {{catalog}}.

    python3 scripts/sql.py -t <target> test_data/evaluate_customers.sql

Statements are split on ';' at line ends and run in order. Writes (e.g. scenario_step2.sql)
need a regular SQL warehouse; if the target's warehouse is Lakehouse//RT (read-only), another
warehouse in the workspace is used for non-SELECT statements.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-t", "--target", required=True)
    ap.add_argument("file")
    args = ap.parse_args()

    d = json.loads(subprocess.run(["databricks", "bundle", "validate", "-t", args.target, "-o", "json"],
                                  capture_output=True, text=True, cwd=ROOT, check=True).stdout)
    v = {k: x.get("value") for k, x in d["variables"].items()}
    profile = d["workspace"]["profile"]

    def cli(*a: str) -> subprocess.CompletedProcess:
        return subprocess.run(["databricks", *a, "--profile", profile, "-o", "json"], capture_output=True, text=True)

    read_wh = write_wh = v["sql_warehouse_id"]
    wh = json.loads(cli("warehouses", "get", read_wh).stdout)
    if wh.get("warehouse_type") == "REALTIME":
        others = [w for w in json.loads(cli("warehouses", "list").stdout) if w.get("warehouse_type") != "REALTIME"]
        write_wh = others[0]["id"] if others else read_wh

    text = Path(args.file).read_text().replace("{{catalog}}", v["catalog"])
    text = "\n".join(line for line in text.splitlines() if not line.strip().startswith("--"))
    for stmt in [s.strip() for s in re.split(r";\s*\n", text) if s.strip()]:
        stmt = stmt.rstrip(";")
        warehouse = read_wh if re.match(r"(?is)^\s*(select|with)\b", stmt) else write_wh
        r = cli("experimental", "aitools", "tools", "query", "--warehouse", warehouse, stmt)
        if r.returncode != 0:
            sys.exit(f"failed: {stmt[:100]}...\n{r.stderr[:1500]}")
        print(r.stdout.strip())


if __name__ == "__main__":
    main()
