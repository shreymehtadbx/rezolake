-- Headline counts for one entity type.
-- @param catalog STRING = your_catalog
-- @param entity_type_id STRING = customer
SELECT
  (SELECT count(*) FROM IDENTIFIER(:catalog || '.rezolake2_records.canonical_record_current')
    WHERE entity_type_id = :entity_type_id AND NOT is_deleted) AS records,
  (SELECT count(DISTINCT source_id) FROM IDENTIFIER(:catalog || '.rezolake2_records.canonical_record_current')
    WHERE entity_type_id = :entity_type_id AND NOT is_deleted) AS sources,
  (SELECT count(*) FROM IDENTIFIER(:catalog || '.rezolake2_master.entity_current')
    WHERE entity_type_id = :entity_type_id) AS entities,
  (SELECT count(*) FROM IDENTIFIER(:catalog || '.rezolake2_master.entity_current')
    WHERE entity_type_id = :entity_type_id AND member_count > 1) AS multi_record_entities,
  (SELECT count(*) FROM IDENTIFIER(:catalog || '.rezolake2_resolution.review_queue_v')
    WHERE entity_type_id = :entity_type_id) AS open_reviews,
  (SELECT count(*) FROM IDENTIFIER(:catalog || '.rezolake2_resolution.manual_constraint')
    WHERE entity_type_id = :entity_type_id AND active) AS active_constraints
