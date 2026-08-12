-- =============================================================
-- 04_template.sql — Workout Templates (user-owned)
-- Run order: 4th (depends on users + exercises)
-- Idempotent: safe to re-run
--
-- A template is a reusable plan, not a log: it mirrors session_exercises
-- minus everything that only makes sense once a session has happened
-- (status, skip_reason, and set_logs altogether).
--
-- Templates are owned by a user, unlike scripts/data/*.yaml which is shared
-- master data. Nothing here is seeded.
-- =============================================================

-- -------------------------------------------------------------
-- workout_templates — a named, reusable training day
-- -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS workout_templates (
    id           SERIAL PRIMARY KEY,
    user_id      INTEGER NOT NULL
                 REFERENCES users (id) ON DELETE CASCADE,
    name         VARCHAR(100) NOT NULL,
    program_day  VARCHAR(64),
    notes        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_template_name_not_blank
        CHECK (length(btrim(name)) > 0),
    -- Scoped to the owner: two users may both have a "Push A".
    CONSTRAINT uq_template_name
        UNIQUE (user_id, name)
);

CREATE INDEX IF NOT EXISTS idx_templates_user
    ON workout_templates (user_id, name);

-- -------------------------------------------------------------
-- template_exercises — one exercise slot within a template
-- -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS template_exercises (
    id                SERIAL PRIMARY KEY,
    template_id       INTEGER NOT NULL
                      REFERENCES workout_templates (id) ON DELETE CASCADE,
    exercise_id       INTEGER NOT NULL
                      REFERENCES exercises (id) ON DELETE RESTRICT,
    order_index       SMALLINT NOT NULL,
    target_sets       SMALLINT,
    target_reps_min   SMALLINT,
    target_reps_max   SMALLINT,
    target_weight_kg  NUMERIC(6, 2),
    notes             TEXT,

    CONSTRAINT ck_tpl_target_sets_positive
        CHECK (target_sets IS NULL OR target_sets > 0),
    CONSTRAINT ck_tpl_target_reps_order
        CHECK (target_reps_min IS NULL OR target_reps_max IS NULL
               OR target_reps_min <= target_reps_max),
    CONSTRAINT uq_template_order
        UNIQUE (template_id, order_index)
);

CREATE INDEX IF NOT EXISTS idx_template_exercises_template
    ON template_exercises (template_id, order_index);
CREATE INDEX IF NOT EXISTS idx_template_exercises_exercise
    ON template_exercises (exercise_id);
