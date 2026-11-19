-- name: installs-monthly
-- metrics: installs
WITH mapped AS (
    SELECT CASE coalesce(nullif(trim(BOTH '_' FROM regexp_replace(lower(trim(m.city)), '[^a-z0-9]+', '_')), ''), 'city_not_found') WHEN 'guntur' THEN 'vijayawada' WHEN 'faridabad' THEN 'delhi' WHEN 'attingal' THEN 'trivandrum' ELSE coalesce(nullif(trim(BOTH '_' FROM regexp_replace(lower(trim(m.city)), '[^a-z0-9]+', '_')), ''), 'city_not_found') END AS canonical_city_key,
           coalesce(nullif(trim(m.city), ''), 'city_not_found') AS source_city,
           i.appsflyer_id
    FROM appsflyer.canonical_appsflyer_customer_install i
    LEFT JOIN marketing.media_source_channel_mapping c
      ON c.identifier = coalesce(i.media_source, i.af_prt, 'organic')
    LEFT JOIN marketing.appsflyer_vs_rapido_internal_mapping m
      ON af_pincode = postal_code
    WHERE i.yyyymmdd >= ? AND i.yyyymmdd < ?
      AND c.channel NOT IN ('Retargeting_repeat', 'Retargeting_new')
      AND length(i.appsflyer_id) != 14
)
SELECT canonical_city_key,
       min(source_city) AS sample_source_city,
       count(DISTINCT appsflyer_id) AS installs
FROM mapped
GROUP BY canonical_city_key
ORDER BY canonical_city_key

-- name: registrations-monthly
-- metrics: registrations
WITH registrations_ranked AS (
    SELECT CASE coalesce(nullif(trim(BOTH '_' FROM regexp_replace(lower(trim(city)), '[^a-z0-9]+', '_')), ''), 'city_not_found') WHEN 'guntur' THEN 'vijayawada' WHEN 'faridabad' THEN 'delhi' WHEN 'attingal' THEN 'trivandrum' ELSE coalesce(nullif(trim(BOTH '_' FROM regexp_replace(lower(trim(city)), '[^a-z0-9]+', '_')), ''), 'city_not_found') END AS canonical_city_key,
           coalesce(nullif(trim(city), ''), 'city_not_found') AS source_city,
           substr(mobile, -10, 10) AS last10,
           row_number() OVER (
               PARTITION BY substr(mobile, -10, 10) ORDER BY yyyymmdd ASC
           ) AS row3
    FROM entity.users_snapshot
    WHERE yyyymmdd >= ? AND yyyymmdd < ?
      AND mobile_verified = TRUE AND roles LIKE '%customer%'
)
SELECT canonical_city_key,
       min(source_city) AS sample_source_city,
       count(DISTINCT last10) AS registrations
FROM registrations_ranked
WHERE row3 = 1
GROUP BY canonical_city_key
ORDER BY canonical_city_key

-- name: ftu-monthly
-- metrics: platform_ftu, platform_link_ftu, platform_auto_ftu, platform_cab_ftu
WITH mapped AS (
    SELECT CASE coalesce(nullif(trim(BOTH '_' FROM regexp_replace(lower(trim(city_name)), '[^a-z0-9]+', '_')), ''), 'city_not_found') WHEN 'guntur' THEN 'vijayawada' WHEN 'faridabad' THEN 'delhi' WHEN 'attingal' THEN 'trivandrum' ELSE coalesce(nullif(trim(BOTH '_' FROM regexp_replace(lower(trim(city_name)), '[^a-z0-9]+', '_')), ''), 'city_not_found') END AS canonical_city_key,
           coalesce(nullif(trim(city_name), ''), 'city_not_found') AS source_city,
           customer_id,
           lower(trim(service_category)) AS service_key,
           platform_level_new_order_flag
    FROM customer.customer_order_channel_attribution_master
    WHERE cast(order_date AS varchar) >= ? AND cast(order_date AS varchar) < ?
)
SELECT canonical_city_key,
       min(source_city) AS sample_source_city,
       count(DISTINCT CASE WHEN platform_level_new_order_flag = 'new' THEN customer_id END) AS platform_ftu,
       count(DISTINCT CASE WHEN platform_level_new_order_flag = 'new' AND service_key = 'link' THEN customer_id END) AS platform_link_ftu,
       count(DISTINCT CASE WHEN platform_level_new_order_flag = 'new' AND service_key = 'auto' THEN customer_id END) AS platform_auto_ftu,
       count(DISTINCT CASE WHEN platform_level_new_order_flag = 'new' AND service_key = 'cab' THEN customer_id END) AS platform_cab_ftu
FROM mapped
GROUP BY canonical_city_key
ORDER BY canonical_city_key
