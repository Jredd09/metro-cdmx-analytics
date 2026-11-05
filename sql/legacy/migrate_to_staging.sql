/* =============================================================================
   Map the original course model (schema `legacy`, Spanish names) onto the new
   model in schema `staging`. Staging has the same tables and columns as
   `metro` but NO constraints, so every legacy row survives and the audit can
   measure how many of them would have been rejected.
   Mapping is faithful to the legacy semantics; nothing is "fixed" here.
   ========================================================================== */

DROP SCHEMA IF EXISTS staging CASCADE;
CREATE SCHEMA staging;
SET search_path TO staging, metro, public;

-- Same shape as metro, constraints stripped (LIKE copies NOT NULL; drop those).
DO $$
DECLARE t TEXT; c RECORD;
BEGIN
    FOREACH t IN ARRAY ARRAY['snapshot','state','line','station','line_station','station_entrance',
                             'transit_service','station_connection','depot','train',
                             'maintenance_event','operator','operator_phone','shift_assignment'] LOOP
        EXECUTE format('CREATE TABLE staging.%I (LIKE metro.%I INCLUDING DEFAULTS)', t, t);
        FOR c IN SELECT column_name FROM information_schema.columns
                 WHERE table_schema = 'staging' AND table_name = t AND is_nullable = 'NO' LOOP
            EXECUTE format('ALTER TABLE staging.%I ALTER COLUMN %I DROP NOT NULL', t, c.column_name);
        END LOOP;
    END LOOP;
END $$;

-- The legacy model stores a redundant train status flag; keep it for auditing.
ALTER TABLE staging.train ADD COLUMN legacy_in_service BOOLEAN;

-- Snapshot = project delivery date.
INSERT INTO staging.snapshot VALUES (DATE '2023-01-01', DATE '2025-12-05');

INSERT INTO staging.state SELECT * FROM metro.state;

INSERT INTO staging.line (line_id, line_code, name)
SELECT numerolinea,
       CASE numerolinea WHEN 10 THEN 'A' WHEN 11 THEN 'B' ELSE numerolinea::TEXT END,
       nombre
FROM legacy.linea;

INSERT INTO staging.station (station_id, name, opens_at, closes_at, manager_name, phone)
SELECT numeroestacion, nombreestacion, horaapertura, horacierre, jefeestacion, telefono
FROM legacy.estacion;

INSERT INTO staging.line_station (line_id, station_id, seq)
SELECT numerolinea, numeroestacion,
       row_number() OVER (PARTITION BY numerolinea ORDER BY numeroestacion)
FROM legacy.pertenecer;

INSERT INTO staging.station_entrance (entrance_id, station_id, label, opens_at, closes_at)
SELECT numeroacceso, numeroestacion, 'Access ' || numeroacceso, horaapertura, horacierre
FROM legacy.acceso;

INSERT INTO staging.transit_service (service_id, name, service_type)
SELECT idservicio, nombre, tipo FROM legacy.servicio;

INSERT INTO staging.station_connection (station_id, service_id)
SELECT numeroestacion, idservicio FROM legacy.conectar;

INSERT INTO staging.depot (depot_id, depot_code, name, station_id)
SELECT idhangar, NULL, 'Hangar ' || idhangar, numeroestacion FROM legacy.hangar;

INSERT INTO staging.train (train_id, line_id, manufacturer, build_year, cars, legacy_in_service)
SELECT idtren, numerolinea, marca, aniofabrica, vagones, estatus FROM legacy.tren;

INSERT INTO staging.maintenance_event (event_id, train_id, depot_id, start_date, end_date)
SELECT row_number() OVER (ORDER BY idtren, fechainicio), idtren, idhangar, fechainicio, fechafin
FROM legacy.reservar;

-- Legacy sex codes: 'M' = masculino, 'F' = femenino  ->  RENAPO 'H' / 'M'.
-- Legacy `Estado` holds a state name; it is mapped to its RENAPO code.
INSERT INTO staging.operator (operator_id, curp, first_name, last_name, second_last_name, sex,
                              birth_date, birth_state_code, monthly_salary, status)
SELECT row_number() OVER (ORDER BY c.curp), c.curp, c.nombre, c.paterno, c.materno,
       CASE c.sexo WHEN 'M' THEN 'H' WHEN 'F' THEN 'M' END,
       c.nacimiento, s.state_code, c.salario, 'active'
FROM legacy.conductor c
LEFT JOIN metro.state s ON lower(s.name) = lower(c.estado);

INSERT INTO staging.operator_phone (operator_id, phone)
SELECT o.operator_id, t.telefono
FROM legacy.telefono t JOIN staging.operator o ON o.curp = t.curp;

-- Shifts: the legacy model stores TIME without a date for the end of the shift,
-- so the end timestamp is taken on the same day, exactly as the model implies.
INSERT INTO staging.shift_assignment (shift_id, operator_id, train_id, service_date, shift_code, start_ts, end_ts)
SELECT row_number() OVER (ORDER BY d.fecha, d.idtren, d.horainicio), o.operator_id, d.idtren, d.fecha, d.turno,
       d.fecha + d.horainicio, d.fecha + d.horafin
FROM legacy.conducir d
JOIN staging.operator o ON o.curp = d.curp;
