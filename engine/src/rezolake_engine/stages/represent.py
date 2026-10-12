"""MATCH REPRESENTATION: canonical record -> match text, normalized features, embedding.

Shared by every blocker and matcher; any index over the table belongs to the
blocker that queries it. Kept separate from the canonical record so swapping
matchers never touches the Entity Definition.
"""

from __future__ import annotations

from .context import RunContext

_NORMALIZE = {
    "email": "lower(trim({v}))",
    "phone": "regexp_replace({v}, '[^0-9]', '')",
    "id": "upper(regexp_replace({v}, '[^A-Za-z0-9]', ''))",
    "code": "upper(regexp_replace({v}, '[^A-Za-z0-9]', ''))",
}
_DEFAULT_NORM = "lower(regexp_replace(trim({v}), '\\\\s+', ' '))"


def represent(ctx: RunContext) -> None:
    ctx.stage("represent")
    d, et, run = ctx.definition, ctx.lit(ctx.entity_type_id), ctx.lit(ctx.run_id)
    model = ctx.strategy.embedding_endpoint
    matching = [a for a in d.attributes if a.matching]
    text_parts = ", ".join(
        f"concat({ctx.lit(a.display_name + ': ')}, c.attributes[{ctx.lit(a.attribute_id)}])" for a in matching
    )
    feat_parts = ", ".join(
        f"{ctx.lit(a.attribute_id)}, "
        + _NORMALIZE.get(a.semantic_type, _DEFAULT_NORM).format(v=f"c.attributes[{ctx.lit(a.attribute_id)}]")
        for a in matching
    )
    full = "TRUE" if ctx.mode == "FULL" else "FALSE"
    stg = f"{ctx.cfg.records}.`_stg_rep_{ctx.run_id[:8]}`"
    ctx.sql(f"""
        CREATE OR REPLACE TABLE {stg} AS
        WITH todo AS (
          SELECT c.record_id, c.record_hash, c.is_deleted,
                 concat_ws('; ', {text_parts}) AS match_text,
                 map_filter(map({feat_parts}), (k, v) -> v IS NOT NULL AND v <> '') AS features
          FROM {ctx.cfg.records}.canonical_record_current c
          LEFT JOIN {ctx.cfg.records}.match_representation m ON m.record_id = c.record_id
          WHERE c.entity_type_id = {et}
            AND ({full} OR m.record_id IS NULL OR m.canonical_hash IS DISTINCT FROM c.record_hash
                 OR m.is_deleted <> c.is_deleted OR m.embedding_model IS DISTINCT FROM {ctx.lit(model)}))
        SELECT t.*,
               ai_query({ctx.lit(model)}, coalesce(nullif(t.match_text, ''), '(empty record)')) AS raw_embedding
        FROM todo t""")
    n = ctx.sql(f"SELECT count(*) n FROM {stg}").first().n
    if n:
        ctx.sql(f"""
            MERGE INTO {ctx.cfg.records}.match_representation t
            USING (
              SELECT record_id, record_hash, is_deleted, match_text, features,
                     CASE WHEN raw_embedding IS NULL THEN NULL ELSE
                       transform(raw_embedding, x -> CAST(x / sqrt(aggregate(raw_embedding, 0D, (a, y) -> a + y * y)) AS FLOAT))
                     END AS embedding
              FROM {stg}) s
            ON t.record_id = s.record_id
            WHEN MATCHED THEN UPDATE SET
              match_text = s.match_text, features = s.features,
              embedding = coalesce(s.embedding, t.embedding), canonical_hash = s.record_hash,
              embedding_model = {ctx.lit(model)}, is_deleted = s.is_deleted,
              updated_at = current_timestamp(), last_run_id = {run}
            WHEN NOT MATCHED THEN INSERT
              (record_id, entity_type_id, match_text, features, embedding, canonical_hash, embedding_model,
               is_deleted, updated_at, last_run_id)
              VALUES (s.record_id, {et}, s.match_text, s.features, s.embedding, s.record_hash, {ctx.lit(model)},
                      s.is_deleted, current_timestamp(), {run})""")
    ctx.sql(f"DROP TABLE IF EXISTS {stg}")
    ctx.representations_changed = n
    ctx.metric(representations_updated=n)
    ctx.log(f"represent: {n} rows embedded/updated with {model}")

