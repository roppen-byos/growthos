-- name: installs-monthly
-- metrics: installs
WITH mapped AS (
    SELECT CASE coalesce(nullif(trim(BOTH '_' FROM regexp_replace(lower(trim(m.city)), '[^a-z0-9]+', '_')), ''), 'city_not_found') WHEN 'unrecognized_city' THEN 'city_not_found' WHEN 'guntur' THEN 'vijayawada' WHEN 'faridabad' THEN 'delhi' WHEN 'attingal' THEN 'trivandrum' ELSE coalesce(nullif(trim(BOTH '_' FROM regexp_replace(lower(trim(m.city)), '[^a-z0-9]+', '_')), ''), 'city_not_found') END AS canonical_city_key,
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
    SELECT CASE coalesce(nullif(trim(BOTH '_' FROM regexp_replace(lower(trim(city)), '[^a-z0-9]+', '_')), ''), 'city_not_found') WHEN 'unrecognized_city' THEN 'city_not_found' WHEN 'guntur' THEN 'vijayawada' WHEN 'faridabad' THEN 'delhi' WHEN 'attingal' THEN 'trivandrum' ELSE coalesce(nullif(trim(BOTH '_' FROM regexp_replace(lower(trim(city)), '[^a-z0-9]+', '_')), ''), 'city_not_found') END AS canonical_city_key,
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
    SELECT CASE coalesce(nullif(trim(BOTH '_' FROM regexp_replace(lower(trim(city_name)), '[^a-z0-9]+', '_')), ''), 'city_not_found') WHEN 'unrecognized_city' THEN 'city_not_found' WHEN 'guntur' THEN 'vijayawada' WHEN 'faridabad' THEN 'delhi' WHEN 'attingal' THEN 'trivandrum' ELSE coalesce(nullif(trim(BOTH '_' FROM regexp_replace(lower(trim(city_name)), '[^a-z0-9]+', '_')), ''), 'city_not_found') END AS canonical_city_key,
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

-- name: funnel-monthly
-- metrics: fe_sessions, rr, net, bike_rr, bike_net, auto_rr, auto_net, cab_rr, cab_net
WITH date_bounds AS (
    SELECT CAST(? AS varchar) AS start_day,
           CAST(? AS varchar) AS end_day
), rf AS (
    SELECT date_trunc('day', date(day)) AS time_value,
           customerid,
           city,
           CAST(max(fe_sessions_unique_daily) AS double) AS fe,
           CAST(sum(rr_sessions_unique_daily) AS double) AS rr,
           CAST(sum(net_rides_daily) AS double) AS net,
           CAST(sum(CASE WHEN service_name IN ('Link', 'Bike Pink', 'Bike Metro', 'Scooty', 'Bike Rated by Women', 'Bike Lite') THEN rr_sessions_unique_daily END) AS double) AS bike_rr,
           CAST(sum(CASE WHEN service_name IN ('Link', 'Bike Pink', 'Bike Metro', 'Scooty', 'Bike Rated by Women', 'Bike Lite') THEN net_rides_daily END) AS double) AS bike_net,
           CAST(sum(CASE WHEN service_name IN ('Auto', 'Auto Lite', 'Auto Pet', 'Auto Pool', 'Auto Priority', 'AutoPremium') THEN rr_sessions_unique_daily END) AS double) AS auto_rr,
           CAST(sum(CASE WHEN service_name IN ('Auto', 'Auto Lite', 'Auto Pet', 'Auto Pool', 'Auto Priority', 'AutoPremium') THEN net_rides_daily END) AS double) AS auto_net,
           CAST(sum(CASE WHEN service_name IN ('CabEconomy', 'CabPremium', 'Cab Premium - Intercity', 'Cab AC - Airport', 'Cab AC Sedan - Airport', 'Cab SUV', 'Cab Premium - Airport', 'Cab AC Sedan', 'Cab Daily', 'Cab Economy - Intercity', 'CabAC', 'Cab Priority', 'Cab SUV - Airport', 'Cab SUV - Intercity', 'Cab Economy - Airport', 'Cab Premium Biz') THEN rr_sessions_unique_daily END) AS double) AS cab_rr,
           CAST(sum(CASE WHEN service_name IN ('CabEconomy', 'CabPremium', 'Cab Premium - Intercity', 'Cab AC - Airport', 'Cab AC Sedan - Airport', 'Cab SUV', 'Cab Premium - Airport', 'Cab AC Sedan', 'Cab Daily', 'Cab Economy - Intercity', 'CabAC', 'Cab Priority', 'Cab SUV - Airport', 'Cab SUV - Intercity', 'Cab Economy - Airport', 'Cab Premium Biz') THEN net_rides_daily END) AS double) AS cab_net
    FROM hive.datasets.customer_rf_daily_kpi_old_ct
    CROSS JOIN date_bounds
    WHERE day >= start_day
      AND day < end_day
    GROUP BY 1,2,3
), ltr_data AS (
    SELECT min(date_trunc('day', date(platform_level_first_order_date))) AS time_value,
           customer_id
    FROM customer_internal.customer_first_ride_lookup_immutable
    CROSS JOIN date_bounds
    WHERE day = date_add('day', -1, date(end_day))
    GROUP BY customer_id
), city_month AS (
    SELECT CASE coalesce(nullif(trim(BOTH '_' FROM regexp_replace(lower(trim(city)), '[^a-z0-9]+', '_')), ''), 'city_not_found') WHEN 'unrecognized_city' THEN 'city_not_found' WHEN 'guntur' THEN 'vijayawada' WHEN 'faridabad' THEN 'delhi' WHEN 'attingal' THEN 'trivandrum' ELSE coalesce(nullif(trim(BOTH '_' FROM regexp_replace(lower(trim(city)), '[^a-z0-9]+', '_')), ''), 'city_not_found') END AS canonical_city_key,
           coalesce(nullif(trim(city), ''), 'city_not_found') AS source_city,
           CAST(coalesce(sum(fe), 0) AS bigint) AS fe_sessions,
           CAST(coalesce(sum(rr), 0) AS bigint) AS rr,
           CAST(coalesce(sum(net), 0) AS bigint) AS net,
           CAST(coalesce(sum(bike_rr), 0) AS bigint) AS bike_rr,
           CAST(coalesce(sum(bike_net), 0) AS bigint) AS bike_net,
           CAST(coalesce(sum(auto_rr), 0) AS bigint) AS auto_rr,
           CAST(coalesce(sum(auto_net), 0) AS bigint) AS auto_net,
           CAST(coalesce(sum(cab_rr), 0) AS bigint) AS cab_rr,
           CAST(coalesce(sum(cab_net), 0) AS bigint) AS cab_net
    FROM rf
    LEFT JOIN ltr_data
        ON rf.customerid = ltr_data.customer_id
    WHERE (
        rf.time_value <= ltr_data.time_value
        OR ltr_data.time_value IS NULL
    )
    GROUP BY 1,2
)
SELECT canonical_city_key,
       min(source_city) AS sample_source_city,
       sum(fe_sessions) AS fe_sessions,
       sum(rr) AS rr,
       sum(net) AS net,
       sum(bike_rr) AS bike_rr,
       sum(bike_net) AS bike_net,
       sum(auto_rr) AS auto_rr,
       sum(auto_net) AS auto_net,
       sum(cab_rr) AS cab_rr,
       sum(cab_net) AS cab_net
FROM city_month
GROUP BY canonical_city_key
ORDER BY canonical_city_key
