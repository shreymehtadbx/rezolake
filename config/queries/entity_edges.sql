-- "Why are these together?": decisions and human constraints touching the entity's records.
-- @param catalog STRING = your_catalog
-- @param entity_type_id STRING = customer
-- @param entity_id STRING = x
WITH members AS (
  SELECT record_id FROM IDENTIFIER(:catalog || '.rezolake2_resolution.entity_membership_current')
  WHERE entity_type_id = :entity_type_id AND entity_id = :entity_id),
decisions AS (
  SELECT d.* FROM IDENTIFIER(:catalog || '.rezolake2_resolution.current_decision_v') d
  WHERE d.entity_type_id = :entity_type_id
    AND (d.left_record_id IN (SELECT record_id FROM members) OR d.right_record_id IN (SELECT record_id FROM members))),
-- Evidence of the scores behind each decision: same pair, same record versions and (for decisions that
-- record their provenance) one of the matchers the decision lists.
evidence AS (
  SELECT d.pair_id, max_by(s.evidence, s.created_at) AS evidence, max_by(s.trace_id, s.created_at) AS trace_id,
         max_by(s.matcher_id, s.created_at) AS matcher_id
  FROM decisions d
  JOIN IDENTIFIER(:catalog || '.rezolake2_resolution.pair_score') s
    ON s.entity_type_id = d.entity_type_id AND s.pair_id = d.pair_id
   AND s.left_hash = d.left_hash AND s.right_hash = d.right_hash
  WHERE d.scores IS NULL
     OR array_contains(transform(from_json(d.scores, 'array<struct<matcher:string>>'), x -> x.matcher),
                       concat(s.matcher_id, '_v', s.matcher_version))
  GROUP BY d.pair_id)
SELECT 'decision' AS kind, d.pair_id, d.left_record_id, d.right_record_id, d.decision AS label,
       d.score, d.reason, d.policy_version AS detail, e.evidence, e.trace_id, e.matcher_id, d.is_current,
       CAST(NULL AS STRING) AS constraint_id, d.created_at,
       (d.left_record_id IN (SELECT record_id FROM members) AND d.right_record_id IN (SELECT record_id FROM members)) AS internal,
       d.strategy_version, d.scores
FROM decisions d
LEFT JOIN evidence e ON e.pair_id = d.pair_id
UNION ALL
SELECT 'constraint', CAST(NULL AS STRING), c.left_record_id, c.right_record_id, c.constraint_type,
       CAST(NULL AS DOUBLE), c.reason, c.created_by, CAST(NULL AS STRING), CAST(NULL AS STRING), CAST(NULL AS STRING),
       TRUE, c.constraint_id, c.created_at,
       (c.left_record_id IN (SELECT record_id FROM members)
        AND (c.right_record_id IS NULL OR c.right_record_id IN (SELECT record_id FROM members))),
       CAST(NULL AS INT), CAST(NULL AS STRING)
FROM IDENTIFIER(:catalog || '.rezolake2_resolution.manual_constraint') c
WHERE c.entity_type_id = :entity_type_id AND c.active
  AND (c.left_record_id IN (SELECT record_id FROM members) OR c.right_record_id IN (SELECT record_id FROM members))
ORDER BY 1, 6 DESC
