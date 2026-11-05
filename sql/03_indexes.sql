/* Secondary indexes for the analytical access paths. The exclusion constraints
   in 01_schema.sql already create GiST indexes on (operator_id, range),
   (train_id, range) and (train_id, daterange) for maintenance. */
SET search_path TO metro, public;

CREATE INDEX IF NOT EXISTS ix_line_station_station   ON line_station (station_id);
CREATE INDEX IF NOT EXISTS ix_train_line             ON train (line_id);
CREATE INDEX IF NOT EXISTS ix_maint_train_start      ON maintenance_event (train_id, start_date);
CREATE INDEX IF NOT EXISTS ix_maint_depot_range      ON maintenance_event USING gist (depot_id, daterange(start_date, end_date, '[)'));
CREATE INDEX IF NOT EXISTS ix_operator_line          ON operator (base_line_id);
CREATE INDEX IF NOT EXISTS ix_shift_date_train       ON shift_assignment (service_date, train_id);
