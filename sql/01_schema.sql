/* =============================================================================
   Metro CDMX Analytics — relational schema (PostgreSQL 14+)
   -----------------------------------------------------------------------------
   Re-designed from a university course model. Key differences vs. the original:
     * Physical stations are modelled once; line membership is a many-to-many
       relation with the station's position along the line (transfer stations
       are no longer duplicated as "Pantitlán L5", "Pantitlán L9", ...).
     * Shifts are stored as timestamp ranges, so overnight shifts are valid and
       overlaps can be rejected by exclusion constraints.
     * Train availability is derived from maintenance records instead of a
       redundant status flag that could contradict them.
     * CURP (Mexican national ID) is validated in the database: format, check
       digit, and consistency with birth date, sex and birth state.
   ========================================================================== */

CREATE EXTENSION IF NOT EXISTS btree_gist;

DROP SCHEMA IF EXISTS metro CASCADE;
CREATE SCHEMA metro;
SET search_path TO metro, public;

-- -----------------------------------------------------------------------------
-- Validation helpers
-- -----------------------------------------------------------------------------

-- Official CURP check digit (position 18) per RENAPO's algorithm.
CREATE FUNCTION curp_check_digit(curp TEXT) RETURNS CHAR(1)
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
    SELECT ((10 - (SUM(
               (strpos('0123456789ABCDEFGHIJKLMNÑOPQRSTUVWXYZ', substr(curp, i, 1)) - 1)
               * (19 - i)) % 10)) % 10)::TEXT
    FROM generate_series(1, 17) AS i
$$;

CREATE FUNCTION curp_is_valid(curp TEXT) RETURNS BOOLEAN
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
    SELECT curp ~ '^[A-Z][AEIOUX][A-Z]{2}[0-9]{6}[HM][A-Z]{2}[B-DF-HJ-NP-TV-Z]{3}[0-9A-Z][0-9]$'
       AND substr(curp, 18, 1) = curp_check_digit(curp)
$$;

-- Dataset metadata: observation window. Views read "today" from here so that
-- results are reproducible regardless of when the database is built.
CREATE TABLE snapshot (
    obs_start  DATE NOT NULL,
    as_of      DATE NOT NULL CHECK (as_of > obs_start)
);
CREATE UNIQUE INDEX snapshot_single_row ON snapshot ((true));

-- -----------------------------------------------------------------------------
-- Catalogs
-- -----------------------------------------------------------------------------

CREATE TABLE state (
    state_code  CHAR(2)      PRIMARY KEY,           -- RENAPO code used inside CURP
    name        VARCHAR(60)  NOT NULL UNIQUE
);

CREATE TABLE line (
    line_id     SMALLINT     PRIMARY KEY,
    line_code   VARCHAR(3)   NOT NULL UNIQUE,       -- '1'..'12', 'A', 'B'
    name        VARCHAR(30)  NOT NULL UNIQUE,
    color       VARCHAR(20)  NOT NULL,
    hex_color   CHAR(7)      NOT NULL CHECK (hex_color ~ '^#[0-9A-F]{6}$'),
    high_ridership BOOLEAN   NOT NULL DEFAULT FALSE  -- stylised demand class used as a covariate
);

CREATE TABLE station (
    station_id     INTEGER      PRIMARY KEY,
    name           VARCHAR(80)  NOT NULL UNIQUE,    -- one row per physical station
    opens_at       TIME         NOT NULL,
    closes_at      TIME         NOT NULL,           -- 00:00 means midnight
    manager_name   VARCHAR(120),
    phone          CHAR(10)     CHECK (phone ~ '^[0-9]{10}$')
);

-- Ordered membership of stations in lines (a transfer station has >1 row).
CREATE TABLE line_station (
    line_id     SMALLINT  NOT NULL REFERENCES line ON UPDATE CASCADE ON DELETE CASCADE,
    station_id  INTEGER   NOT NULL REFERENCES station ON UPDATE CASCADE ON DELETE RESTRICT,
    seq         SMALLINT  NOT NULL CHECK (seq > 0),
    PRIMARY KEY (line_id, station_id),
    UNIQUE (line_id, seq)
);

CREATE TABLE station_entrance (
    entrance_id  INTEGER  PRIMARY KEY,
    station_id   INTEGER  NOT NULL REFERENCES station ON UPDATE CASCADE ON DELETE CASCADE,
    label        VARCHAR(40) NOT NULL,
    opens_at     TIME     NOT NULL,
    closes_at    TIME     NOT NULL,
    UNIQUE (station_id, label)
);

CREATE TABLE transit_service (
    service_id    INTEGER      PRIMARY KEY,
    name          VARCHAR(60)  NOT NULL UNIQUE,
    service_type  VARCHAR(30)  NOT NULL
);

CREATE TABLE station_connection (
    station_id  INTEGER  NOT NULL REFERENCES station ON UPDATE CASCADE ON DELETE CASCADE,
    service_id  INTEGER  NOT NULL REFERENCES transit_service ON UPDATE CASCADE ON DELETE CASCADE,
    PRIMARY KEY (station_id, service_id)
);

CREATE TABLE depot (
    depot_id    SMALLINT     PRIMARY KEY,
    depot_code  CHAR(3)      NOT NULL UNIQUE,
    name        VARCHAR(60)  NOT NULL UNIQUE,
    station_id  INTEGER      NOT NULL REFERENCES station ON UPDATE CASCADE ON DELETE RESTRICT,
    bays        SMALLINT     NOT NULL CHECK (bays > 0)   -- simultaneous trains it can hold
);

-- -----------------------------------------------------------------------------
-- Rolling stock and maintenance
-- -----------------------------------------------------------------------------

CREATE TABLE train (
    train_id        INTEGER      PRIMARY KEY,
    line_id         SMALLINT     NOT NULL REFERENCES line ON UPDATE CASCADE ON DELETE RESTRICT,
    home_depot_id   SMALLINT     NOT NULL REFERENCES depot ON UPDATE CASCADE ON DELETE RESTRICT,
    manufacturer    VARCHAR(30)  NOT NULL,
    model           VARCHAR(20)  NOT NULL,
    build_year      SMALLINT     NOT NULL CHECK (build_year BETWEEN 1969 AND 2035),
    cars            SMALLINT     NOT NULL CHECK (cars IN (6, 7, 9))
);

CREATE TABLE maintenance_event (
    event_id     INTEGER      PRIMARY KEY,
    train_id     INTEGER      NOT NULL REFERENCES train ON UPDATE CASCADE ON DELETE CASCADE,
    depot_id     SMALLINT     NOT NULL REFERENCES depot ON UPDATE CASCADE ON DELETE RESTRICT,
    kind         VARCHAR(12)  NOT NULL CHECK (kind IN ('corrective', 'preventive')),
    start_date   DATE         NOT NULL,
    end_date     DATE,                                -- NULL = still in the depot
    CHECK (end_date IS NULL OR end_date > start_date),
    -- A train cannot be in two maintenance events at once.
    EXCLUDE USING gist (train_id WITH =, daterange(start_date, end_date, '[)') WITH &&)
);

-- -----------------------------------------------------------------------------
-- Workforce
-- -----------------------------------------------------------------------------

CREATE TABLE operator (
    operator_id       INTEGER       PRIMARY KEY,
    curp              CHAR(18)      NOT NULL UNIQUE,
    first_name        VARCHAR(60)   NOT NULL,
    last_name         VARCHAR(60)   NOT NULL,
    second_last_name  VARCHAR(60),
    sex               CHAR(1)       NOT NULL CHECK (sex IN ('H', 'M')),   -- RENAPO: H = male, M = female
    birth_date        DATE          NOT NULL,
    birth_state_code  CHAR(2)       NOT NULL REFERENCES state,
    hire_date         DATE          NOT NULL,
    base_line_id      SMALLINT      NOT NULL REFERENCES line,
    monthly_salary    NUMERIC(10,2) NOT NULL CHECK (monthly_salary > 0),
    status            VARCHAR(10)   NOT NULL CHECK (status IN ('active', 'leave', 'terminated')),
    CONSTRAINT operator_adult_at_hire  CHECK (hire_date >= birth_date + INTERVAL '18 years'),
    CONSTRAINT curp_valid              CHECK (curp_is_valid(curp)),
    CONSTRAINT curp_matches_birth_date CHECK (substr(curp, 5, 6) = to_char(birth_date, 'YYMMDD')),
    CONSTRAINT curp_matches_sex        CHECK (substr(curp, 11, 1) = sex),
    CONSTRAINT curp_matches_state      CHECK (substr(curp, 12, 2) = birth_state_code)
);

CREATE TABLE operator_phone (
    operator_id  INTEGER   NOT NULL REFERENCES operator ON UPDATE CASCADE ON DELETE CASCADE,
    phone        CHAR(10)  NOT NULL CHECK (phone ~ '^[0-9]{10}$'),
    PRIMARY KEY (operator_id, phone)
);

-- Who drove which train, when. Ranges are half-open [start, end).
CREATE TABLE shift_assignment (
    shift_id      BIGINT     PRIMARY KEY,
    operator_id   INTEGER    NOT NULL REFERENCES operator ON UPDATE CASCADE ON DELETE CASCADE,
    train_id      INTEGER    NOT NULL REFERENCES train ON UPDATE CASCADE ON DELETE CASCADE,
    service_date  DATE       NOT NULL,
    shift_code    CHAR(1)    NOT NULL CHECK (shift_code IN ('M', 'V', 'N')),  -- morning / afternoon / night
    start_ts      TIMESTAMP  NOT NULL,
    end_ts        TIMESTAMP  NOT NULL,
    CHECK (end_ts > start_ts AND end_ts - start_ts <= INTERVAL '12 hours'),
    CHECK (start_ts::date = service_date),
    -- Nobody drives two trains at once, and no train has two drivers at once.
    EXCLUDE USING gist (operator_id WITH =, tsrange(start_ts, end_ts) WITH &&),
    EXCLUDE USING gist (train_id    WITH =, tsrange(start_ts, end_ts) WITH &&)
);

-- A train that is in the depot on a given day cannot be assigned a driver.
CREATE FUNCTION trg_block_shift_on_maintenance() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM maintenance_event m
        WHERE m.train_id = NEW.train_id
          AND daterange(m.start_date, m.end_date, '[)') @> NEW.service_date
    ) THEN
        RAISE EXCEPTION 'train % is under maintenance on %', NEW.train_id, NEW.service_date;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER shift_requires_available_train
    BEFORE INSERT OR UPDATE ON shift_assignment
    FOR EACH ROW EXECUTE FUNCTION trg_block_shift_on_maintenance();
