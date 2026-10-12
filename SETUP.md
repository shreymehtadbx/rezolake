# Setting up Rezolake in your Databricks workspace

This guide takes the repo from GitHub to a working Rezolake in **your** workspace, catalog and Lakebase,
using a Declarative Automation Bundle (DABs). One command does it; the manual steps are below if you prefer.
For what gets built and why, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

Validated end to end on a brand-new workspace on 2026-10-08 (`setup.py --with-fixtures`: app, engine, Lakebase,
Delta schemas and a smoke test at precision 1.0 / recall 1.0, about 25 minutes including the npm-registry retry).

## 1. Prerequisites

**On your machine**

| Tool | Version | Check |
| --- | --- | --- |
| Databricks CLI | 1.17+ | `databricks --version` |
| `uv` (builds the engine wheel) | any recent | `uv --version` |
| Node.js + npm | Node 22+ | `node --version` |
| Python | 3.10+ | `python3 --version` |

Authenticate the CLI to your workspace once (OAuth):

```bash
databricks auth login --host https://<your-workspace>.cloud.databricks.com
```

The CLI opens a browser to sign in, then asks for a **profile name** (it suggests one; press Enter to accept
it or type your own) and saves the profile in `~/.databrickscfg`. **Note the profile name:** you pass it to
`setup.py --profile` next. `databricks auth profiles` lists your saved profiles if you forget it.

**In the workspace** (ask a workspace admin if unsure)

| Need | Why | Notes |
| --- | --- | --- |
| Unity Catalog, a **catalog you can `CREATE SCHEMA` in** | Rezolake's Delta tables (`rezolake2_*` schemas) | A managed catalog. For a Lakehouse//RT warehouse it must not use default storage |
| A **SQL warehouse** (serverless or Lakehouse//RT) | The app reads mastered data through it | The Serverless Starter Warehouse works |
| **Databricks Apps**, **serverless jobs** (environment version 6), **Lakebase Autoscaling** | App, engine job, operational state | Setup creates the Lakebase project if it doesn't exist |
| Serving endpoints **`databricks-bge-large-en`** and **`databricks-claude-sonnet-4-6`** | Embeddings and the default (LLM) matcher | Foundation Model APIs; both can be changed per entity type later |
| Optional: **AI Decide** preview | Only for the `ai_decide` matcher | Settings > Previews; setup warns if it's off |
| Optional: **Vector Search** | Only for the `ai_search_blocker` | Created with `--with-ai-search` |

## 2. One-command setup

```bash
git clone https://github.com/<org>/rezolake.git && cd rezolake
python3 scripts/setup.py --profile <profile-name> --with-fixtures
```

`setup.py` asks for the catalog and warehouse when there is more than one candidate (or pass
`--catalog`, `--warehouse`, `--yes`). It:

1. checks the tools and that the CLI profile is authenticated;
2. lists the catalogs you can create schemas in and the serverless / RT warehouses, and proposes a choice;
3. writes the bundle target **`targets/<target>.yml`** (default target name: the profile name) and runs
   `databricks bundle validate` on it;
4. runs **`scripts/bootstrap.py -t <target>`**, which
   * checks the catalog, warehouse and serving endpoints (and warns if AI Decide is off),
   * creates the **Lakebase project** (`production` branch, `primary` endpoint) if missing,
   * runs **`databricks bundle deploy`** (engine job + wheel + app) and **`databricks bundle run app`**
     (the app creates its Lakebase schema on first start),
   * runs the engine's **`SETUP`** mode (Delta schemas, tables, views; read grants for the app's service principal),
   * with `--with-fixtures`, loads a small 3-source customer test dataset into `<catalog>.rezolake2_fixtures`;
5. with `--with-fixtures`, runs **`scripts/smoke_test.py`**: creates a `customer` entity type over the test
   data through the app's API, publishes it, runs a FULL resolution and scores it against the ground truth,
   ending with `SMOKE TEST PASSED`.

It prints the app URL at the end. Everything is idempotent: re-running reuses the target file (pass
`--rewrite` to regenerate it) and skips what already exists.

| Option | Default | |
| --- | --- | --- |
| `--target` | the profile name | Bundle target / environment name |
| `--catalog` | asked | Catalog for the `rezolake2_*` schemas |
| `--warehouse` | asked | SQL warehouse id |
| `--lakebase-project` | `projects/rezolake` | Created if missing |
| `--app-name` | `rezolake` | Change it to run two environments in one workspace |
| `--npm-registry` | `auto` | Where the app build gets npm packages: `proxy` (the workspace's Databricks npm proxy), `public` (registry.npmjs.org), or `auto` — proxy, switching to public and retrying if the proxy is missing packages. See [npm registry](#npm-registry) |
| `--with-fixtures` | off | Test data + smoke test (recommended the first time) |
| `--with-ai-search` | off | Also create the AI Search endpoint |
| `--yes` | off | Take the proposed choices without prompting |

## 3. Manual setup (DABs)

The same steps by hand, if you want to control each one.

1. **Add a target.** Create `targets/<env>.yml` (it is picked up by `include: targets/*.yml` in
   `databricks.yml`; `targets/prod.yml` is an example):

   ```yaml
   targets:
     myenv:
       workspace:
         host: https://<your-workspace>.cloud.databricks.com
         profile: <profile-name>
       variables:
         catalog: <your_catalog>
         sql_warehouse_id: "<warehouse id>"
         lakebase_project: projects/rezolake      # created by bootstrap if missing
         # app_name: rezolake                      # default
         # vs_endpoint: rezolake-vs-endpoint       # default; only for the AI Search blocker
   ```

   Everything else (Lakebase branch / database / endpoint, engine parameters, the app's catalog) is derived
   in `databricks.yml`; the app reads its catalog from the engine job's `catalog` parameter.

2. **Validate:** `databricks bundle validate -t myenv`
3. **Bootstrap** (Lakebase, deploy, app, `SETUP`): `npm install && python3 scripts/bootstrap.py -t myenv --with-fixtures`
4. **Verify:** `python3 scripts/smoke_test.py -t myenv`

Plain `databricks bundle deploy -t myenv && databricks bundle run app -t myenv` is all you need for later
code updates; bootstrap is only for the first time (or to re-check an environment).

## 4. After setup

* **Open the app** (URL printed by setup, or Databricks Apps > `rezolake`) and give your stewards
  `CAN_USE` on it.
* **Your own data:** create an entity type, add your source Delta tables (each needs a primary key), map
  their columns to attributes, publish and run. Rezolake never modifies sources. Enable change data feed on
  them for incremental runs (`ALTER TABLE … SET TBLPROPERTIES (delta.enableChangeDataFeed = true)`); without
  it every run snapshot-diffs the source.
* **Automation:** the Runs tab can create a trigger job per entity type (on table update or a schedule). Grant
  the app's service principal `SELECT` on the watched source tables.
* **Remove the test data** when you no longer need it: delete the `customer` entity type in the app and
  `DROP SCHEMA <catalog>.rezolake2_fixtures CASCADE`.

### npm registry

The app is built inside the workspace (Databricks Apps runs `npm install` + `npm run build` on deploy). By
default it fetches packages through the workspace's Databricks npm proxy, which is the only option in workspaces
with network egress controls. Some regions' proxies are missing packages: the app deployment then fails with
`Error installing packages`, and the app log shows `npm error 404 … is not in this registry` (seen in October
2026, when one region's proxy lacked 91 of the app's 982 packages).

For such an environment, the app build can use the public npm registry instead. `setup.py` does this
automatically (`--npm-registry auto`) or on request (`--npm-registry public`). It adds this to the target file:

```yaml
    sync:
      include:
        - ../.npmrc
```

and bootstrap writes `.npmrc` from `scripts/templates/npmrc.public` (`registry=https://registry.npmjs.org/`,
`replace-registry-host=always`; the lockfile's integrity hashes still apply). `.npmrc` is git-ignored, so other
environments keep using the proxy. If you deploy such a target from a fresh clone with plain
`databricks bundle deploy`, run `python3 scripts/bootstrap.py -t <target> --skip-deploy` once first, or copy
the template to `.npmrc`.

## 5. Day-to-day

| Task | Command |
| --- | --- |
| Deploy code to an environment | `databricks bundle deploy -t <t> && databricks bundle run app -t <t>` |
| Run the app locally against an environment | `python3 scripts/dev_env.py -t <t> && npm run dev` (deploy the target first) |
| Run the engine locally against an environment | `engine/run_local.sh -t <t> INCREMENTAL customer` (first: `cd engine && uv venv && uv pip install -e . databricks-connect`; Python 3.12 pinned) |
| Run a test-data SQL file | `python3 scripts/sql.py -t <t> test_data/scenario_step2.sql` |
| Regenerate query types after editing `config/queries/*.sql` | `python3 scripts/dev_env.py -t prod && set -a && . ./.env && set +a && npx appkit generate-types --wait`, then commit `shared/appkit-types/` |

Query types: the `-- @param … = <sample>` values in `config/queries/*.sql` name the **prod** catalog, so type
generation (including the one AppKit's dev server runs on startup) only produces real types against `prod`.
Running `npm run dev` against another environment degrades `shared/appkit-types/analytics.d.ts` to
`result: unknown`; don't commit that (`npm run typecheck` fails on it via `typegen:check`) — restore with
`git checkout shared/appkit-types`, or point the samples at your catalog if your environment is the canonical one.

Rules: bump `engine/pyproject.toml` `version` on every engine change (serverless caches wheels by version);
deploy shared environments only from `main`.

### Using your own source tables

Point entity-type sources at any UC Delta tables. Rezolake never modifies sources. For incremental ingest,
enable change data feed on them (`ALTER TABLE … SET TBLPROPERTIES (delta.enableChangeDataFeed = true)`);
otherwise every run does a snapshot diff. For automatic (table-update) orchestration, grant the app's service
principal `SELECT` on the watched tables.

## Permissions model

| Identity | Needs |
| --- | --- |
| Deployer (you) | workspace user with UC `CREATE SCHEMA` on the catalog, can create jobs/apps/Lakebase/VS; owner of the Lakebase project |
| Engine job (runs as the deployer) | `SELECT` on sources, `ALL` on `rezolake2_*` (owns them), Lakebase role (project owner has one), `CAN_QUERY` on serving endpoints |
| App service principal | `CAN_USE` warehouse, `CAN_CONNECT_AND_CREATE` Lakebase, `CAN_MANAGE_RUN` engine job (all from the bundle), `USE CATALOG` + `SELECT` on `rezolake2_*` (from SETUP), `SELECT` on sources only for triggers |
| Stewards (app users) | `CAN_USE` the app; UC `SELECT` on candidate source tables to browse/preview them (previews run as the user) |


## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `profile … is not authenticated` / `cannot configure default credentials` | CLI token expired | `databricks auth login --profile <profile>` |
| `workspace_id mismatch` on `bundle deploy` | The profile was re-pointed at another workspace and kept the old `workspace_id` | Delete the `workspace_id =` line for the profile in `~/.databrickscfg` (or use a new profile) |
| `lifecycle.started is only supported in direct deployment mode` | A failed deploy left Terraform state | Delete `.databricks/bundle/<target>` and redeploy (`bundle.engine: direct` is set) |
| Engine run fails with `Unexpected error during library installation` | A dependency no longer installs on the job's serverless environment | The job uses environment version 6 and `mlflow-skinny`; to find the culprit, submit a run with one task per dependency |
| Runs fail with `REQUEST_LIMIT_EXCEEDED` | Foundation Model API per-minute token limit | The LLM matcher backs off and retries; lower its concurrency in the Strategy tab for large first runs |
| App deployment fails with `Error installing packages`; app log shows `npm error 404` | The workspace's npm proxy is missing packages | Re-run setup (`--npm-registry auto` handles it) or pass `--npm-registry public`; see [npm registry](#npm-registry) |
| `AI Decide Preview is not enabled` | Preview off | Only needed for the `ai_decide` matcher: Settings > Previews |
| Smoke test precision below 1.0 | Expected: the test data has two different "John Smith"s a matcher can merge | Split them in the Entities tab (that's the stewardship demo) |
