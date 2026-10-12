-- Entity explorer page: search, filter, sort and paginate mastered entities on the server.
-- @param catalog STRING = your_catalog
-- @param entity_type_id STRING = customer
-- @param search STRING =
-- @param source_id STRING =
-- @param size STRING = all
-- @param review STRING = all
-- @param sort STRING = size
-- @param limit INT = 50
-- @param offset INT = 0
WITH srcs AS (
  SELECT entity_id, array_sort(collect_set(source_id)) AS sources
  FROM IDENTIFIER(:catalog || '.rezolake2_master.entity_crosswalk_v')
  WHERE entity_type_id = :entity_type_id
  GROUP BY entity_id),
reviews AS (
  SELECT m.entity_id, count(DISTINCT q.pair_id) AS open_reviews
  FROM IDENTIFIER(:catalog || '.rezolake2_resolution.review_queue_v') q
  JOIN IDENTIFIER(:catalog || '.rezolake2_resolution.entity_membership_current') m
    ON m.entity_type_id = q.entity_type_id AND m.record_id IN (q.left_record_id, q.right_record_id)
  WHERE q.entity_type_id = :entity_type_id
  GROUP BY m.entity_id)
SELECT e.entity_id, e.display_name, e.member_count, e.source_count, e.golden, e.updated_at,
       to_json(coalesce(s.sources, array())) AS sources,
       CAST(coalesce(r.open_reviews, 0) AS INT) AS open_reviews,
       count(*) OVER () AS total_count
FROM IDENTIFIER(:catalog || '.rezolake2_master.entity_current') e
LEFT JOIN srcs s ON s.entity_id = e.entity_id
LEFT JOIN reviews r ON r.entity_id = e.entity_id
WHERE e.entity_type_id = :entity_type_id
  AND (:search = ''
       OR lower(e.golden) LIKE '%' || lower(:search) || '%'
       OR e.entity_id = :search
       OR e.entity_id IN (
            SELECT x.entity_id FROM IDENTIFIER(:catalog || '.rezolake2_master.entity_crosswalk_v') x
            WHERE x.entity_type_id = :entity_type_id AND (x.source_key = :search OR x.record_id = :search)))
  AND (:source_id = '' OR array_contains(s.sources, :source_id))
  AND (:size = 'all' OR (:size = 'multi' AND e.member_count > 1) OR (:size = 'single' AND e.member_count = 1))
  AND (:review = 'all' OR coalesce(r.open_reviews, 0) > 0)
ORDER BY CASE WHEN :sort = 'size' THEN e.member_count END DESC,
         CASE WHEN :sort = 'reviews' THEN coalesce(r.open_reviews, 0) END DESC,
         CASE WHEN :sort = 'updated' THEN e.updated_at END DESC,
         e.display_name, e.entity_id
LIMIT :limit OFFSET :offset
