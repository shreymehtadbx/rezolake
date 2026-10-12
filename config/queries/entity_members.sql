-- Member records: canonical attributes, extensions and the raw source payload.
-- @param catalog STRING = your_catalog
-- @param entity_type_id STRING = customer
-- @param entity_id STRING = x
SELECT m.record_id, c.source_id, c.source_key, to_json(c.attributes) AS attributes_json,
       c.additional_attributes, s.payload, m.joined_at, c.updated_at,
       (e.anchor_record_id = m.record_id) AS is_anchor
FROM IDENTIFIER(:catalog || '.rezolake2_resolution.entity_membership_current') m
JOIN IDENTIFIER(:catalog || '.rezolake2_records.canonical_record_current') c
  ON c.entity_type_id = m.entity_type_id AND c.record_id = m.record_id
JOIN IDENTIFIER(:catalog || '.rezolake2_records.source_record_current') s
  ON s.entity_type_id = m.entity_type_id AND s.record_id = m.record_id
JOIN IDENTIFIER(:catalog || '.rezolake2_master.entity_current') e
  ON e.entity_type_id = m.entity_type_id AND e.entity_id = m.entity_id
WHERE m.entity_type_id = :entity_type_id AND m.entity_id = :entity_id
ORDER BY c.source_id, c.source_key
