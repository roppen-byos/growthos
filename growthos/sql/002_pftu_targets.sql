-- Apply after 001_reporting.sql, which creates schema irf.
-- Independent plan data. No FK to replaceable actual-month metadata.
-- The owner import owns the transaction and explicitly grants reader SELECT.
CREATE TABLE irf.pftu_targets (
    month date NOT NULL CHECK (EXTRACT(DAY FROM month) = 1),
    kind text NOT NULL CHECK (kind IN ('city', 'new_launch', 'overall')),
    city_key text NOT NULL,
    city text NOT NULL CHECK (length(city) BETWEEN 1 AND 120),
    target bigint CHECK (target >= 0),
    source_targets jsonb NOT NULL CHECK (jsonb_typeof(source_targets) = 'array'
        AND jsonb_array_length(source_targets) BETWEEN 1 AND 100),
    source_checksum text NOT NULL CHECK (source_checksum ~ '^[0-9a-f]{64}$'),
    imported_at timestamptz NOT NULL,
    month_checksum text NOT NULL CHECK (month_checksum ~ '^[0-9a-f]{64}$'),
    month_row_count integer NOT NULL CHECK (month_row_count BETWEEN 3 AND 20000),
    PRIMARY KEY (month, kind, city_key),
    CHECK ((kind = 'city' AND city_key ~ '^[a-z0-9][a-z0-9_]{0,119}$')
        OR (kind = 'new_launch' AND city_key = '' AND city = 'New launch')
        OR (kind = 'overall' AND city_key = '' AND city = 'Grand Total'))
);
REVOKE ALL ON irf.pftu_targets FROM PUBLIC;
