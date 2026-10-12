-- Resolve a source record (source_id + source key) to its master entity and siblings.
-- @param catalog STRING = your_catalog
-- @param entity_type_id STRING = customer
-- @param source_id STRING = crm
-- @param source_key STRING = C0001
WITH hit AS (
  SELECT entity_id FROM IDENTIFIER(:catalog || '.rezolake2_master.entity_crosswalk_v')
  WHERE entity_type_id = :entity_type_id AND source_id = :source_id AND source_key = :source_key)
SELECT x.entity_id, x.record_id, x.source_id, x.source_key, x.is_anchor,
       (x.source_id = :source_id AND x.source_key = :source_key) AS is_query
FROM IDENTIFIER(:catalog || '.rezolake2_master.entity_crosswalk_v') x
JOIN hit h ON h.entity_id = x.entity_id
WHERE x.entity_type_id = :entity_type_id
ORDER BY is_query DESC, x.source_id, x.source_key
