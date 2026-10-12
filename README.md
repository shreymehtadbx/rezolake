# Rezolake — MDM on Databricks (AppKit)

Rezolake is a master-data-management / entity-resolution app that runs entirely inside a Databricks
workspace, built on **AppKit** with a **Delta-authoritative** resolution engine.

```
            Rezolake app (AppKit: Node/React)                 rezolake-resolve (serverless Lakeflow Job, Python)
 ┌──────────────────────────────────────────────┐        ┌──────────────────────────────────────────────────┐
 │ Entity types · definition wizard · strategy  │ jobs() │ SETUP · PUBLISH · INCREMENTAL · FULL · LIMITED ·  │
 │ runs · review queue · entity explorer ·      │ ─────▶ │ COMMAND                                           │
 │ steward commands                             │        │ ingest → canonicalize → represent → block →       │
 │   lakebase(): drafts, commands, run status   │        │ match → decide → cluster → survive               │
 │   analytics(): reads via Lakehouse//RT       │        └───────────────┬──────────────────────────────────┘
 └──────────────┬───────────────────────────────┘                        │ writes
                │ reads (RT warehouse, read-only)                        ▼
                └──────────────────────────────▶  Unity Catalog Delta: rezolake2_meta / _records / _resolution / _master
```

* **Delta is the MDM system of record**: source ledger, canonical records, candidate pairs, pair scores,
  decisions, human constraints, membership with stable entity ids, golden attributes with provenance,
  run manifests. Nothing important is overwritten; current state is derived.
* **Lakebase holds operational state only**: entity-type drafts, strategy drafts, steward commands, run
  progress, review notes.
* **Lakehouse//RT is read-only** (no DDL/DML/MERGE), so every write goes through the engine job; the app
  reads mastered data through `config/queries/*.sql`.

**Full design — components, every Delta and Lakebase table, identifiers and incremental semantics,
strategies, stewardship, operations and open items: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** (also as
[PDF](docs/ARCHITECTURE.pdf)).

## Layout

| Path | What |
| --- | --- |
| `server/` | AppKit server: Lakebase schema (`lib/schema.ts`), REST API (`routes/api.ts`) |
| `client/` | React UI (entity types, definition wizard, strategy, runs, review, explorer, activity) |
| `config/queries/` | Warehouse SQL read by the UI (`*.obo.sql` runs as the signed-in user) |
| `engine/` | `rezolake_engine` wheel: contracts, components, stages, CLI entry point |
| `engine/src/rezolake_engine/components/` | Pluggable components (NEAREST BY / AI Search blockers, ai_decide / LLM matchers, threshold policy, constrained clustering, survivorship) + registry |
| `test_data/` | Messy 3-source customer fixture with ground truth + evaluation SQL |
| `databricks.yml`, `targets/` | Bundle: app + engine job + wheel artifact; one target file per environment |

## Component contracts

| Stage | Contract (Delta) | MVP component |
| --- | --- | --- |
| Blocker | `candidate_pair` (left < right, rank, block_score) | `nearest_by_blocker` v1 (default) — SQL `NEAREST BY` join over `match_representation`, no index; `ai_search_blocker` v1 — AI Search ANN over its own Delta Sync index (creates and syncs it) |
| Matcher | `pair_score` (score = P(match), evidence, trace_id) — negatives kept | `llm_entity_matcher` v1 (default) — DSPy EntityMatcher with a rationale per decision; `ai_decide_matcher` v1 — `ai_decide` decision model per pair via REST, P(match) without rationale (Beta). Both use the entity-kind rules |
| Decision | `resolution_decision` LINK / REVIEW / NO_LINK | `threshold_policy` v1 |
| Clusterer | `entity_membership_current`, `entity_event`, `entity_alias` | `constrained_components` v1 (CANNOT > MUST > auto LINK; stable ids) |
| Survivorship | `entity_attribute_current` (+ wide `<slug>_entity`) | `attribute_rules` v1 (source priority / recency / most common / longest; overrides; pins) |

Stewardship writes durable constraints/overrides (`manual_constraint`, `record_override`,
`survivorship_override`) through `resolution_command`s, so automated reruns never undo a human decision.

## Set up in your workspace

```bash
databricks auth login --host https://<your-workspace>.cloud.databricks.com   # prompts for a profile name; note it
python3 scripts/setup.py --profile <profile-name> --with-fixtures
```

That writes a bundle target (`targets/<profile>.yml`), deploys the app and engine with DABs, creates the
Delta schemas and Lakebase project, loads a small test dataset and runs a smoke test. Prerequisites, options,
the manual DABs steps and troubleshooting: **[SETUP.md](SETUP.md)**.

## Environments

Each environment is a bundle target in `targets/<name>.yml`, included by `databricks.yml`. There is no
default target, so every bundle command and script names one with `-t`.

| Target | Workspace | Use |
| --- | --- | --- |
| `prod` | `<your-workspace>` · profile `DEFAULT` | the live app; deploy only from `main` |
| `hr-dev` | `<your-dev-workspace>` · profile `<your-dev-profile>` | dev / test environment (set up with `scripts/setup.py`) |

## Develop

```bash
npm install
cd engine && uv venv && uv pip install -e . pytest databricks-connect && .venv/bin/python -m pytest && cd ..
npm run typecheck && npm run lint
databricks bundle deploy -t <target> && databricks bundle run app -t <target>   # deploy
engine/run_local.sh -t <target> INCREMENTAL customer                           # engine locally (Databricks Connect)
python3 scripts/dev_env.py -t <target> && npm run dev                          # app locally against a target
```

Workflow: branch per feature from `main` → deploy/test on a dev/test target → PR → merge → `bundle deploy -t prod`.
Bump `engine/pyproject.toml` `version` whenever engine code changes so serverless reinstalls the wheel.
The engine job runs on serverless environment 6 and depends on `mlflow-skinny` (tracing only); see
[docs/ARCHITECTURE.md §8](docs/ARCHITECTURE.md#8-operations).
Query types in `shared/appkit-types/` are committed; regenerate with `npx appkit generate-types --wait`
(`.env` loaded) after editing `config/queries/*.sql`.


## Test fixture

`test_data/generate_customers.py <catalog>` (run by `bootstrap.py --with-fixtures`) produces `rezolake2_fixtures.{crm_contacts, erp_customers,
web_signups, truth}` (CDF on). `python3 scripts/sql.py -t <target> test_data/evaluate_customers.sql` computes pairwise precision/recall of the
resolved entities against the truth; `scenario_step2.sql` applies the incremental acceptance-scenario changes.
