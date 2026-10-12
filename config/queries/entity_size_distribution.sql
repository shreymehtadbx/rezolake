-- Entities by number of member records (1, 2, 3, 4, 5+) for one entity type.
-- @param catalog STRING = your_catalog
-- @param entity_type_id STRING = customer
SELECT CASE WHEN member_count >= 5 THEN '5+' ELSE CAST(member_count AS STRING) END AS records_per_entity,
       count(*) AS entities
FROM IDENTIFIER(:catalog || '.rezolake2_master.entity_current')
WHERE entity_type_id = :entity_type_id
GROUP BY 1
ORDER BY 1
