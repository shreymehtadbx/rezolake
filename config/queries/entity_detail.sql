-- One entity (following merge aliases) with its golden record.
-- @param catalog STRING = your_catalog
-- @param entity_type_id STRING = customer
-- @param entity_id STRING = x
WITH target AS (
  SELECT coalesce(
    (SELECT current_entity_id FROM IDENTIFIER(:catalog || '.rezolake2_resolution.entity_alias')
      WHERE entity_type_id = :entity_type_id AND old_entity_id = :entity_id LIMIT 1), :entity_id) AS entity_id)
SELECT e.entity_id, e.anchor_record_id, e.display_name, e.member_count, e.source_count, e.golden,
       e.created_at, e.updated_at, (e.entity_id <> :entity_id) AS followed_alias
FROM IDENTIFIER(:catalog || '.rezolake2_master.entity_current') e
JOIN target t ON t.entity_id = e.entity_id
WHERE e.entity_type_id = :entity_type_id
