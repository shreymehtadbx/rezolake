-- Open review cases: current REVIEW decisions with no human constraint, side by side.
-- @param catalog STRING = your_catalog
-- @param entity_type_id STRING = customer
-- @param limit INT = 50
WITH scores AS (
  -- Evidence of the scores behind each decision (same pair, same record versions, a listed matcher).
  SELECT q.pair_id, max_by(s.evidence, s.created_at) AS evidence, max_by(s.trace_id, s.created_at) AS trace_id
  FROM IDENTIFIER(:catalog || '.rezolake2_resolution.review_queue_v') q
  JOIN IDENTIFIER(:catalog || '.rezolake2_resolution.pair_score') s
    ON s.entity_type_id = q.entity_type_id AND s.pair_id = q.pair_id
   AND s.left_hash = q.left_hash AND s.right_hash = q.right_hash
  WHERE q.entity_type_id = :entity_type_id
    AND (q.scores IS NULL
         OR array_contains(transform(from_json(q.scores, 'array<struct<matcher:string>>'), x -> x.matcher),
                           concat(s.matcher_id, '_v', s.matcher_version)))
  GROUP BY q.pair_id)
SELECT q.pair_id, q.left_record_id, q.right_record_id, q.score, q.reason, q.created_at,
       l.source_id AS left_source, l.source_key AS left_key, to_json(l.attributes) AS left_attributes,
       r.source_id AS right_source, r.source_key AS right_key, to_json(r.attributes) AS right_attributes,
       s.evidence, s.trace_id
FROM IDENTIFIER(:catalog || '.rezolake2_resolution.review_queue_v') q
JOIN IDENTIFIER(:catalog || '.rezolake2_records.canonical_record_current') l
  ON l.entity_type_id = q.entity_type_id AND l.record_id = q.left_record_id
JOIN IDENTIFIER(:catalog || '.rezolake2_records.canonical_record_current') r
  ON r.entity_type_id = q.entity_type_id AND r.record_id = q.right_record_id
LEFT JOIN scores s ON s.pair_id = q.pair_id
WHERE q.entity_type_id = :entity_type_id
ORDER BY q.score DESC
LIMIT :limit
