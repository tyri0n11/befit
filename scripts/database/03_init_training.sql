-- =============================================================
-- 03_training.sql — Workout Logging (Core)
-- Run order: 3rd (depends on users + exercises)
-- Idempotent: safe to re-run
--
-- Volume is NEVER stored — computed at query time:
--   tonnage = weight_kg * (2 if is_unilateral else 1) * reps
--   bodyweight (weight_kg IS NULL) -> tracked via reps
-- =============================================================

-- -------------------------------------------------------------
-- Enum types
-- -------------------------------------------------------------

DO $$ BEGIN
    CREATE TYPE session_status AS ENUM (
        'planned', 'in_progress', 'completed', 'skipped'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE exercise_status AS ENUM (
        'planned', 'completed', 'skipped'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- -------------------------------------------------------------
-- workout_sessions — one training day
-- -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS workout_sessions (
    id            SERIAL PRIMARY KEY,
    user_id       INTEGER NOT NULL
                  REFERENCES users (id) ON DELETE CASCADE,
    session_date  DATE NOT NULL,
    status        session_status NOT NULL DEFAULT 'planned',
    bodyweight_kg NUMERIC(5, 2),
    program_day   VARCHAR(64),
    notes         TEXT,
    -- Soft delete: a session is training history, so removing one must be
    -- undoable. NULL means live; every read path filters on it.
    deleted_at    TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_session_bw_range
        CHECK (bodyweight_kg IS NULL
               OR (bodyweight_kg > 20 AND bodyweight_kg < 300))
);

-- Deliberately not partial here: on a database that predates deleted_at the
-- CREATE TABLE above is skipped, so the column does not exist yet at this
-- point. The backfill block at the bottom swaps this for the partial index,
-- which is where every read path's WHERE clause is actually served from.
CREATE INDEX IF NOT EXISTS idx_sessions_user_date
    ON workout_sessions (user_id, session_date DESC);

-- -------------------------------------------------------------
-- session_exercises — one exercise within a session
-- -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS session_exercises (
    id                SERIAL PRIMARY KEY,
    session_id        INTEGER NOT NULL
                      REFERENCES workout_sessions (id) ON DELETE CASCADE,
    exercise_id       INTEGER NOT NULL
                      REFERENCES exercises (id) ON DELETE RESTRICT,
    order_index       SMALLINT NOT NULL,
    status            exercise_status NOT NULL DEFAULT 'planned',
    target_sets       SMALLINT,
    target_reps_min   SMALLINT,
    target_reps_max   SMALLINT,
    target_weight_kg  NUMERIC(6, 2),
    skip_reason       SMALLINT,
    notes             TEXT,

    CONSTRAINT ck_target_sets_positive
        CHECK (target_sets IS NULL OR target_sets > 0),
    CONSTRAINT ck_target_reps_order
        CHECK (target_reps_min IS NULL OR target_reps_max IS NULL
               OR target_reps_min <= target_reps_max),
    CONSTRAINT uq_session_order
        UNIQUE (session_id, order_index)
);

CREATE INDEX IF NOT EXISTS idx_session_exercises_session
    ON session_exercises (session_id, order_index);
CREATE INDEX IF NOT EXISTS idx_session_exercises_exercise
    ON session_exercises (exercise_id);

-- -------------------------------------------------------------
-- set_logs — one row per set
-- -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS set_logs (
    id                   SERIAL PRIMARY KEY,
    session_exercise_id  INTEGER NOT NULL
                         REFERENCES session_exercises (id) ON DELETE CASCADE,
    set_index            SMALLINT NOT NULL,
    weight_kg            NUMERIC(6, 2),
    reps                 SMALLINT NOT NULL,
    rpe                  NUMERIC(3, 1),
    rest_before_sec      SMALLINT,
    rom_note             TEXT,
    logged_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_reps_positive CHECK (reps > 0),
    CONSTRAINT ck_weight_nonneg CHECK (weight_kg IS NULL OR weight_kg >= 0),
    CONSTRAINT ck_rpe_range     CHECK (rpe IS NULL OR (rpe >= 5 AND rpe <= 10)),
    CONSTRAINT ck_rest_nonneg
        CHECK (rest_before_sec IS NULL OR rest_before_sec >= 0),
    CONSTRAINT uq_set_index
        UNIQUE (session_exercise_id, set_index)
);

CREATE INDEX IF NOT EXISTS idx_set_logs_session_exercise
    ON set_logs (session_exercise_id, set_index);

-- -------------------------------------------------------------
-- Column backfill — patches databases created before soft delete.
-- No-op on a fresh database, where the column above already exists.
-- CREATE TABLE IF NOT EXISTS skips existing tables, so editing the
-- CREATE alone would never reach a live database.
-- -------------------------------------------------------------

ALTER TABLE workout_sessions
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

-- Now that the column is guaranteed to exist, replace the plain index with a
-- partial one: lists, stats and lookups all ask for live rows, so the deleted
-- ones stay out of the index entirely.
DROP INDEX IF EXISTS idx_sessions_user_date;

CREATE INDEX IF NOT EXISTS idx_sessions_user_date
    ON workout_sessions (user_id, session_date DESC)
    WHERE deleted_at IS NULL;
