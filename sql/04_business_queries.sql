/* =============================================================================
   Business questions answered in SQL (PostgreSQL). Run after the pipeline:
       psql "$DATABASE_URL" -f sql/04_business_queries.sql
   ========================================================================== */
SET search_path TO metro, public;

-- Q1. Which lines are least reliable? (KPIs over the full history and last 12 months)
SELECT line_code, fleet, avg_age_years, failures_12m, failures_per_train_year, mtbf_days, availability_12m_pct
FROM v_line_kpi
ORDER BY failures_per_train_year DESC;

-- Q2. Ten trains with the most unplanned downtime in 2025, ranked within their line.
WITH downtime AS (
    SELECT t.train_id, l.line_code, t.manufacturer, t.build_year,
           count(*)                                      AS failures,
           sum(coalesce(m.end_date, sn.as_of + 1) - m.start_date) AS depot_days
    FROM maintenance_event m
    JOIN train t USING (train_id)
    JOIN line l USING (line_id)
    CROSS JOIN snapshot sn
    WHERE m.kind = 'corrective' AND m.start_date >= make_date(extract(year FROM sn.as_of)::INT, 1, 1)
    GROUP BY t.train_id, l.line_code, t.manufacturer, t.build_year
)
SELECT *, rank() OVER (PARTITION BY line_code ORDER BY depot_days DESC) AS rank_in_line
FROM downtime
ORDER BY depot_days DESC
LIMIT 10;

-- Q3. Mean time between failures by manufacturer and model (in-service days per failure).
SELECT manufacturer, model,
       count(DISTINCT train_id)                         AS trains,
       sum(failed)                                      AS failures,
       round(sum(duration_days)::NUMERIC / nullif(sum(failed), 0), 0) AS mtbf_days
FROM v_failure_spell
GROUP BY manufacturer, model
ORDER BY mtbf_days;

-- Q4. Is reliability getting worse? Year-over-year failures per line with growth rate.
WITH yearly AS (
    SELECT l.line_code, extract(year FROM m.start_date)::INT AS yr, count(*) AS failures
    FROM maintenance_event m JOIN train t USING (train_id) JOIN line l USING (line_id)
    WHERE m.kind = 'corrective'
    GROUP BY l.line_code, yr
)
SELECT line_code, yr, failures,
       round(100.0 * (failures - lag(failures) OVER w) / lag(failures) OVER w, 1) AS yoy_pct
FROM yearly
WINDOW w AS (PARTITION BY line_code ORDER BY yr)
ORDER BY line_code, yr;

-- Q5. Transfer stations: the network's busiest interchanges and the services they connect to.
SELECT ts.name, ts.n_lines, ts.lines,
       string_agg(DISTINCT sv.name, ', ') AS other_services
FROM v_transfer_station ts
LEFT JOIN station_connection sc USING (station_id)
LEFT JOIN transit_service sv USING (service_id)
GROUP BY ts.name, ts.n_lines, ts.lines
ORDER BY ts.n_lines DESC, ts.name;

-- Q6. Staffing gaps: line/shift combinations below 95% coverage in the last quarter.
SELECT line_code, shift_code,
       sum(trains_available) AS train_shifts_needed,
       sum(trains_staffed)   AS train_shifts_staffed,
       round(100.0 * sum(trains_staffed) / sum(trains_available), 1) AS coverage_pct
FROM v_staffing_coverage
GROUP BY line_code, shift_code
HAVING sum(trains_staffed)::NUMERIC / sum(trains_available) < 0.95
ORDER BY coverage_pct;

-- Q7. Succession risk: share of each line's active drivers aged 58+ (retirement in < 5 years).
SELECT line_code,
       count(*)                                                 AS active_operators,
       count(*) FILTER (WHERE age_years >= 58)                  AS aged_58_plus,
       round(100.0 * count(*) FILTER (WHERE age_years >= 58) / count(*), 1) AS pct_58_plus,
       round(avg(tenure_years)::NUMERIC, 1)                     AS avg_tenure_years
FROM v_operator_profile
WHERE status = 'active'
GROUP BY line_code
ORDER BY pct_58_plus DESC;

-- Q8. Repair quality: share of corrective repairs followed by another failure within
--     30 days of the train returning to service ("repeat failures"), by manufacturer.
WITH seq AS (
    SELECT m.train_id, t.manufacturer, m.kind, m.start_date, m.end_date,
           lead(m.kind)       OVER w AS next_kind,
           lead(m.start_date) OVER w AS next_start
    FROM maintenance_event m JOIN train t USING (train_id)
    WINDOW w AS (PARTITION BY m.train_id ORDER BY m.start_date)
)
SELECT manufacturer,
       count(*) AS corrective_repairs,
       count(*) FILTER (WHERE next_kind = 'corrective' AND next_start - end_date <= 30) AS repeat_within_30d,
       round(100.0 * count(*) FILTER (WHERE next_kind = 'corrective' AND next_start - end_date <= 30)
             / count(*), 1) AS repeat_rate_pct
FROM seq
WHERE kind = 'corrective' AND end_date IS NOT NULL
GROUP BY manufacturer
ORDER BY repeat_rate_pct DESC;

-- Q9. Depot pressure: peak simultaneous trains vs. bays.
SELECT depot_code, name, bays, max(trains_in_depot) AS peak,
       round(100.0 * avg(over_capacity::INT), 2) AS pct_days_over_capacity
FROM v_depot_occupancy
GROUP BY depot_code, name, bays
ORDER BY pct_days_over_capacity DESC, peak DESC;

-- Q10. Prove the constraints work: each statement below must FAIL.
--      (Wrapped in a transaction that is rolled back.)
-- BEGIN;
-- INSERT INTO operator VALUES (99999, 'XXXX000101HDFXXX00', 'Test', 'Test', NULL, 'H',
--     '2010-01-01', 'DF', '2025-01-01', 1, 15000, 'active');          -- underage + bad CURP
-- INSERT INTO shift_assignment SELECT 99999999, operator_id, train_id, service_date, shift_code,
--     start_ts, end_ts FROM shift_assignment LIMIT 1;                 -- overlapping shift
-- ROLLBACK;
