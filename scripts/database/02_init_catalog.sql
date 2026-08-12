-- =============================================================
-- 02_catalog.sql — Master Data: Muscle Groups & Exercises
-- Run order: 2nd (training domain depends on exercises)
-- Idempotent: safe to re-run
--
-- NOTE: The "at least one primary muscle per exercise" rule is
-- enforced in the SERVICE / SEED layer, not in the database.
-- Every write path must go through that validation.
-- =============================================================

-- -------------------------------------------------------------
-- Enum types
-- -------------------------------------------------------------

DO $$ BEGIN
    CREATE TYPE movement_pattern AS ENUM (
        'squat', 'hinge', 'lunge',
        'horizontal_push', 'vertical_push',
        'horizontal_pull', 'vertical_pull',
        'isolation', 'carry', 'core'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE equipment_type AS ENUM (
        'barbell', 'dumbbell', 'machine', 'cable',
        'bodyweight', 'kettlebell', 'band', 'smith_machine'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE muscle_role AS ENUM ('primary', 'secondary', 'stabilizer');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE force_type AS ENUM ('push', 'pull', 'static');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- -------------------------------------------------------------
-- muscle_groups — self-referencing tree (max depth 2)
-- -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS muscle_groups (
    id            SERIAL PRIMARY KEY,
    code          VARCHAR(32) NOT NULL UNIQUE,
    name_en       VARCHAR(64) NOT NULL,
    name_vi       VARCHAR(64) NOT NULL,
    parent_id     INTEGER REFERENCES muscle_groups (id) ON DELETE RESTRICT,
    depth         SMALLINT NOT NULL DEFAULT 0,
    is_trackable  BOOLEAN  NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_no_self_parent CHECK (parent_id IS NULL OR parent_id <> id),
    CONSTRAINT ck_depth_range    CHECK (depth BETWEEN 0 AND 2)
);

CREATE INDEX IF NOT EXISTS idx_muscle_groups_parent
    ON muscle_groups (parent_id);

-- -------------------------------------------------------------
-- exercises
-- -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS exercises (
    id                 SERIAL PRIMARY KEY,
    slug               VARCHAR(64) NOT NULL UNIQUE,
    name_en            VARCHAR(100) NOT NULL,
    name_vi            VARCHAR(100),
    pattern            movement_pattern NOT NULL,
    equipment          equipment_type NOT NULL,
    force              force_type NOT NULL,
    is_unilateral      BOOLEAN NOT NULL DEFAULT FALSE,
    default_rest_sec   SMALLINT NOT NULL DEFAULT 90,
    requires_overhead  BOOLEAN NOT NULL DEFAULT FALSE,
    notes              TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_rest_sec_positive CHECK (default_rest_sec > 0),
    CONSTRAINT ck_slug_format
        CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$')
);

CREATE INDEX IF NOT EXISTS idx_exercises_pattern_equipment
    ON exercises (pattern, equipment);

-- -------------------------------------------------------------
-- exercise_muscles — M:N junction
-- -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS exercise_muscles (
    exercise_id      INTEGER NOT NULL
                     REFERENCES exercises (id) ON DELETE CASCADE,
    muscle_group_id  INTEGER NOT NULL
                     REFERENCES muscle_groups (id) ON DELETE RESTRICT,
    role             muscle_role NOT NULL,

    PRIMARY KEY (exercise_id, muscle_group_id)
);

CREATE INDEX IF NOT EXISTS idx_exercise_muscles_muscle
    ON exercise_muscles (muscle_group_id, role);

-- -------------------------------------------------------------
-- Trigger: only leaf nodes (is_trackable = TRUE) may be mapped
-- -------------------------------------------------------------

CREATE OR REPLACE FUNCTION check_muscle_is_leaf()
RETURNS TRIGGER AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM muscle_groups
        WHERE id = NEW.muscle_group_id AND is_trackable = FALSE
    ) THEN
        RAISE EXCEPTION
            'MUSCLE_NOT_TRACKABLE: muscle_group_id=% is a parent node',
            NEW.muscle_group_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_exercise_muscles_leaf_only ON exercise_muscles;
CREATE TRIGGER trg_exercise_muscles_leaf_only
    BEFORE INSERT OR UPDATE ON exercise_muscles
    FOR EACH ROW EXECUTE FUNCTION check_muscle_is_leaf();