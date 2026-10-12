-- Sample rows of a candidate source table, read as the signed-in user (UC permissions apply).
-- @param source_table STRING = your_catalog.rezolake2_meta.entity_definition_version
SELECT to_json(struct(*)) AS row_json
FROM IDENTIFIER(:source_table)
LIMIT 10
