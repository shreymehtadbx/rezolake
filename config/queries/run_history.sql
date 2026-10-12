-- Immutable run manifests + metrics from Delta.
-- @param catalog STRING = your_catalog
-- @param entity_type_id STRING = customer
SELECT r.run_id, r.mode, r.trigger_source, r.status, r.manifest, r.metrics, r.error, r.started_at, r.completed_at,
       (SELECT to_json(collect_list(named_struct('source_id', s.source_id, 'read_mode', s.read_mode,
                 'start_version', s.start_version, 'end_version', s.end_version,
                 'inserted', s.inserted, 'updated', s.updated, 'deleted', s.deleted)))
        FROM IDENTIFIER(:catalog || '.rezolake2_meta.resolution_run_source') s WHERE s.run_id = r.run_id) AS sources
FROM IDENTIFIER(:catalog || '.rezolake2_meta.resolution_run') r
WHERE r.entity_type_id = :entity_type_id
ORDER BY r.started_at DESC
LIMIT 25
