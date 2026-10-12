-- Lineage: create / merge / split / retire events involving the entity.
-- @param catalog STRING = your_catalog
-- @param entity_type_id STRING = customer
-- @param entity_id STRING = x
SELECT event_type, entity_id, source_entity_ids, target_entity_ids, run_id, command_id, created_at
FROM IDENTIFIER(:catalog || '.rezolake2_resolution.entity_event')
WHERE entity_type_id = :entity_type_id
  AND (entity_id = :entity_id OR array_contains(source_entity_ids, :entity_id)
       OR array_contains(target_entity_ids, :entity_id))
ORDER BY created_at DESC
LIMIT 50
