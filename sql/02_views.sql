/* =============================================================================
   Analytical views. Every "current" figure is computed relative to
   snapshot.as_of, never CURRENT_DATE, so results are reproducible.
   ========================================================================== */
SET search_path TO metro, public;

-- Train age (years) on a given date, assuming mid-year delivery.
CREATE OR REPLACE FUNCTION train_age_years(day DATE, build_year SMALLINT) RETURNS NUMERIC
LANGUAGE sql IMMUTABLE AS $$ SELECT (day - make_date(build_year, 7, 1)) / 365.25 $$;

-- Transfer stations and the lines they connect.
CREATE OR REPLACE VIEW v_transfer_station AS
SELECT s.station_id, s.name,
       count(*)                                         AS n_lines,
       string_agg(l.line_code, ', ' ORDER BY l.line_id) AS lines
FROM station s
JOIN line_station ls USING (station_id)
JOIN line l USING (line_id)
GROUP BY s.station_id, s.name
HAVING count(*) > 1;

-- Current status of every train, derived from maintenance records.
CREATE OR REPLACE VIEW v_train_status AS
SELECT t.*, l.line_code, l.high_ridership,
       round(train_age_years(sn.as_of, t.build_year), 1) AS age_years,
       m.event_id IS NOT NULL                           AS in_depot,
       m.kind                                           AS depot_reason,
       m.start_date                                     AS in_depot_since
FROM train t
JOIN line l USING (line_id)
CROSS JOIN snapshot sn
LEFT JOIN maintenance_event m
       ON m.train_id = t.train_id
      AND daterange(m.start_date, m.end_date, '[)') @> sn.as_of;

/* Survival dataset: one row per in-service spell, i.e. the time a train runs
   between leaving the depot and its next depot visit.
     failed = 1 -> the spell ended in a corrective (unplanned) repair
     failed = 0 -> right-censored: ended by preventive maintenance or by the
                   end of observation                                         */
CREATE OR REPLACE VIEW v_failure_spell AS
WITH ordered AS (
    SELECT m.train_id, m.kind, m.start_date, m.end_date,
           lag(m.end_date) OVER (PARTITION BY m.train_id ORDER BY m.start_date) AS prev_end,
           row_number()    OVER (PARTITION BY m.train_id ORDER BY m.start_date) AS rn
    FROM maintenance_event m
),
closed_spells AS (            -- spells that end with a depot visit
    SELECT o.train_id,
           CASE WHEN o.rn = 1 THEN greatest(sn.obs_start, make_date(t.build_year, 7, 1))
                ELSE o.prev_end END                                 AS spell_start,
           o.start_date                                             AS spell_end,
           (o.kind = 'corrective')::INT                             AS failed
    FROM ordered o JOIN train t USING (train_id) CROSS JOIN snapshot sn
),
open_spells AS (              -- trains running on the snapshot date
    SELECT t.train_id,
           coalesce(max(m.end_date), greatest(sn.obs_start, make_date(t.build_year, 7, 1))) AS spell_start,
           sn.as_of                                AS spell_end,
           0                                       AS failed
    FROM train t
    CROSS JOIN snapshot sn
    LEFT JOIN maintenance_event m USING (train_id)
    GROUP BY t.train_id, t.build_year, sn.obs_start, sn.as_of
    HAVING bool_and(m.end_date IS NOT NULL) OR count(m.event_id) = 0
),
spells AS (
    SELECT * FROM closed_spells UNION ALL SELECT * FROM open_spells
)
SELECT row_number() OVER (ORDER BY s.train_id, s.spell_start) AS spell_id,
       s.train_id, t.line_id, l.line_code, t.manufacturer, t.model, t.build_year,
       l.high_ridership::INT                           AS high_ridership,
       s.spell_start, s.spell_end,
       (s.spell_end - s.spell_start)                   AS duration_days,
       s.failed,
       train_age_years(s.spell_start, t.build_year)::FLOAT AS age_years_at_start
FROM spells s
JOIN train t USING (train_id)
JOIN line l  USING (line_id)
WHERE s.spell_end > s.spell_start;

-- Days each train spent in a depot, clipped to an arbitrary window.
CREATE OR REPLACE FUNCTION depot_days(p_train INT, p_from DATE, p_to DATE) RETURNS INT
LANGUAGE sql STABLE AS $$
    SELECT coalesce(sum(upper(r) - lower(r)), 0)::INT
    FROM (
        SELECT daterange(m.start_date, m.end_date, '[)') * daterange(p_from, p_to, '[)') AS r
        FROM maintenance_event m
        WHERE m.train_id = p_train
    ) x
    WHERE NOT isempty(r)
$$;

-- Reliability and availability KPIs per line.
CREATE OR REPLACE VIEW v_line_kpi AS
WITH per_train AS (
    SELECT t.train_id, t.line_id,
           train_age_years(sn.as_of, t.build_year)                         AS age,
           depot_days(t.train_id, sn.as_of - 364, sn.as_of + 1)            AS depot_days_12m,
           depot_days(t.train_id, sn.obs_start, sn.as_of + 1)              AS depot_days_all,
           -- days in the fleet within the observation window (later deliveries count from delivery)
           (sn.as_of - greatest(sn.obs_start, make_date(t.build_year, 7, 1)) + 1) AS window_days,
           least(365, sn.as_of - greatest(sn.obs_start, make_date(t.build_year, 7, 1)) + 1) AS window_days_12m
    FROM train t CROSS JOIN snapshot sn
),
failures AS (
    SELECT t.line_id,
           count(*) FILTER (WHERE m.kind = 'corrective')                               AS failures_all,
           count(*) FILTER (WHERE m.kind = 'corrective' AND m.start_date > sn.as_of - 365) AS failures_12m
    FROM maintenance_event m JOIN train t USING (train_id) CROSS JOIN snapshot sn
    GROUP BY t.line_id
)
SELECT l.line_id, l.line_code, l.name,
       count(p.train_id)                                                   AS fleet,
       round(avg(p.age), 1)                                                AS avg_age_years,
       f.failures_12m,
       round(f.failures_all::NUMERIC / (sum(p.window_days) / 365.25), 2)   AS failures_per_train_year,
       round((sum(p.window_days) - sum(p.depot_days_all))::NUMERIC
             / nullif(f.failures_all, 0), 0)                               AS mtbf_days,
       round(100 * (1 - sum(p.depot_days_12m)::NUMERIC / sum(p.window_days_12m)), 1) AS availability_12m_pct
FROM line l
JOIN per_train p USING (line_id)
JOIN failures f  USING (line_id)
GROUP BY l.line_id, l.line_code, l.name, f.failures_12m, f.failures_all;

-- Daily depot occupancy versus capacity over the whole history.
CREATE OR REPLACE VIEW v_depot_occupancy AS
SELECT d.depot_id, d.depot_code, d.name, d.bays, g.day::DATE AS day,
       count(m.event_id)                    AS trains_in_depot,
       count(m.event_id) > d.bays           AS over_capacity
FROM depot d
CROSS JOIN snapshot sn
CROSS JOIN LATERAL generate_series(sn.obs_start, sn.as_of, INTERVAL '1 day') AS g(day)
LEFT JOIN maintenance_event m
       ON m.depot_id = d.depot_id
      AND daterange(m.start_date, m.end_date, '[)') @> g.day::DATE
GROUP BY d.depot_id, d.depot_code, d.name, d.bays, g.day;

-- Staffing coverage: share of available trains that got a driver, per shift.
CREATE OR REPLACE VIEW v_staffing_coverage AS
WITH days AS (
    SELECT g::DATE AS service_date
    FROM generate_series((SELECT min(service_date) FROM shift_assignment),
                         (SELECT max(service_date) FROM shift_assignment), INTERVAL '1 day') g
),
available AS (
    SELECT d.service_date, t.line_id, count(*) AS trains_available
    FROM days d
    CROSS JOIN train t
    WHERE NOT EXISTS (
        SELECT 1 FROM maintenance_event m
        WHERE m.train_id = t.train_id
          AND daterange(m.start_date, m.end_date, '[)') @> d.service_date)
    GROUP BY d.service_date, t.line_id
),
assigned AS (
    SELECT service_date, t.line_id, shift_code, count(*) AS trains_staffed
    FROM shift_assignment s JOIN train t USING (train_id)
    GROUP BY service_date, t.line_id, shift_code
)
SELECT a.service_date, l.line_code, c.shift_code,
       a.trains_available,
       coalesce(s.trains_staffed, 0)                                       AS trains_staffed,
       round(coalesce(s.trains_staffed, 0)::NUMERIC / a.trains_available, 3) AS coverage
FROM available a
JOIN line l USING (line_id)
CROSS JOIN (VALUES ('M'), ('V'), ('N')) AS c(shift_code)
LEFT JOIN assigned s
       ON s.service_date = a.service_date AND s.line_id = a.line_id AND s.shift_code = c.shift_code;

-- Operator profile used by the workforce analysis.
CREATE OR REPLACE VIEW v_operator_profile AS
SELECT o.operator_id, o.sex, o.status, l.line_code, o.monthly_salary::FLOAT AS monthly_salary,
       ((sn.as_of - o.birth_date) / 365.25)::FLOAT  AS age_years,
       ((sn.as_of - o.hire_date)  / 365.25)::FLOAT  AS tenure_years,
       count(s.shift_id)                              AS shifts_in_window,
       coalesce(sum(extract(epoch FROM s.end_ts - s.start_ts)) / 3600, 0)::FLOAT AS hours_in_window
FROM operator o
JOIN line l ON l.line_id = o.base_line_id
CROSS JOIN snapshot sn
LEFT JOIN shift_assignment s USING (operator_id)
GROUP BY o.operator_id, l.line_code, sn.as_of;
