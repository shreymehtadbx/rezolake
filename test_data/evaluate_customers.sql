-- Pairwise precision / recall / F1 of Rezolake's entity membership vs. the fixture truth.
WITH m AS (
  SELECT c.source_id, c.source_key, m.entity_id
  FROM {{catalog}}.rezolake2_resolution.entity_membership_current m
  JOIN {{catalog}}.rezolake2_records.canonical_record_current c
    ON c.entity_type_id = m.entity_type_id AND c.record_id = m.record_id
  WHERE m.entity_type_id = 'customer'),
j AS (
  SELECT t.source_id, t.source_key, t.person_id, m.entity_id
  FROM {{catalog}}.rezolake2_fixtures.truth t
  JOIN m ON m.source_id = t.source_id AND m.source_key = t.source_key),
pairs AS (
  SELECT (a.person_id = b.person_id) AS truth_same, (a.entity_id = b.entity_id) AS pred_same
  FROM j a JOIN j b ON (a.source_id, a.source_key) < (b.source_id, b.source_key))
SELECT
  (SELECT count(*) FROM j) AS records_evaluated,
  (SELECT count(DISTINCT entity_id) FROM j) AS predicted_entities,
  (SELECT count(DISTINCT person_id) FROM j) AS true_entities,
  sum(CASE WHEN truth_same AND pred_same THEN 1 ELSE 0 END) AS tp,
  sum(CASE WHEN NOT truth_same AND pred_same THEN 1 ELSE 0 END) AS fp,
  sum(CASE WHEN truth_same AND NOT pred_same THEN 1 ELSE 0 END) AS fn,
  round(sum(CASE WHEN truth_same AND pred_same THEN 1 ELSE 0 END) / nullif(sum(CASE WHEN pred_same THEN 1 ELSE 0 END), 0), 3) AS precision,
  round(sum(CASE WHEN truth_same AND pred_same THEN 1 ELSE 0 END) / nullif(sum(CASE WHEN truth_same THEN 1 ELSE 0 END), 0), 3) AS recall
FROM pairs
