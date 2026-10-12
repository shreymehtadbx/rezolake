-- Golden attributes with provenance (winning record/source, rule, observed values).
-- @param catalog STRING = your_catalog
-- @param entity_type_id STRING = customer
-- @param entity_id STRING = x
SELECT attribute_id, attribute_name, value, winning_record_id, winning_source_id, rule,
       manual_override, observed, updated_at
FROM IDENTIFIER(:catalog || '.rezolake2_master.entity_attribute_current')
WHERE entity_type_id = :entity_type_id AND entity_id = :entity_id
