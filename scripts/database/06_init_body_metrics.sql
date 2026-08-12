-- =============================================================
-- 06_init_body_metrics.sql — Body Metrics Log
-- Run order: 6th (depends on users only)
-- Idempotent: safe to re-run
--
-- Tracks weight/body-fat/muscle-mass/InBody-style readings over time, one
-- row per user per day. This is a log, not a snapshot — unlike
-- user_profiles.weight_kg (the current value BMR/TDEE is computed from), a
-- row here is never overwritten by a later day's entry, only by a same-day
-- re-log (see uq_metrics_user_date and the upsert-by-date behavior in
-- app/services/body_metrics.py).
--
-- measured_bmr_kcal is a device reading (bioelectrical impedance, e.g.
-- InBody), kept distinct from user_profiles.bmr_kcal, which is *calculated*
-- (Mifflin-St Jeor) — the two are expected to disagree and neither
-- overwrites the other.
-- =============================================================

-- -------------------------------------------------------------
-- body_metrics_logs — one row per user per day
-- -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS body_metrics_logs (
    id                  SERIAL PRIMARY KEY,
    user_id             INTEGER NOT NULL
                        REFERENCES users (id) ON DELETE CASCADE,
    measured_at         DATE NOT NULL DEFAULT CURRENT_DATE,
    weight_kg           NUMERIC(5, 2),
    body_fat_percent    NUMERIC(4, 1),
    muscle_mass_kg      NUMERIC(5, 2),
    visceral_fat_level  SMALLINT,
    measured_bmr_kcal   NUMERIC(6, 1),
    -- Free-form InBody fields not worth a dedicated column (body water %,
    -- protein/mineral mass, waist-hip ratio, ...). Never read by SQL, only
    -- round-tripped through the API — see BodyMetricsLogCreate.extra.
    extra               JSONB,
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_metrics_weight_range
        CHECK (weight_kg IS NULL OR (weight_kg > 20 AND weight_kg < 300)),
    CONSTRAINT ck_metrics_fat_range
        CHECK (body_fat_percent IS NULL OR (body_fat_percent >= 0 AND body_fat_percent <= 70)),
    CONSTRAINT ck_metrics_muscle_range
        CHECK (muscle_mass_kg IS NULL OR (muscle_mass_kg > 0 AND muscle_mass_kg < 200)),
    CONSTRAINT ck_metrics_visceral_fat_range
        CHECK (visceral_fat_level IS NULL OR (visceral_fat_level > 0 AND visceral_fat_level < 60)),
    CONSTRAINT ck_metrics_bmr_range
        CHECK (measured_bmr_kcal IS NULL OR (measured_bmr_kcal > 500 AND measured_bmr_kcal < 5000)),
    -- A row that carries none of these isn't a log entry. extra doesn't count
    -- on its own — it's a companion to at least one real measurement.
    CONSTRAINT ck_metrics_has_data
        CHECK (weight_kg IS NOT NULL OR body_fat_percent IS NOT NULL
               OR muscle_mass_kg IS NOT NULL OR visceral_fat_level IS NOT NULL
               OR measured_bmr_kcal IS NOT NULL),
    -- One entry per day; logging again the same day updates it in place
    -- (see get_or_create_for_date in app/repositories/body_metrics.py).
    CONSTRAINT uq_metrics_user_date
        UNIQUE (user_id, measured_at)
);

CREATE INDEX IF NOT EXISTS idx_body_metrics_user_date
    ON body_metrics_logs (user_id, measured_at DESC);

-- -------------------------------------------------------------
-- Backfill — patches a database created before InBody-style fields
-- (visceral_fat_level, measured_bmr_kcal, extra) existed. No-op on a
-- fresh database, where the CREATE TABLE above already has them.
-- -------------------------------------------------------------

ALTER TABLE body_metrics_logs
    ADD COLUMN IF NOT EXISTS visceral_fat_level SMALLINT;
ALTER TABLE body_metrics_logs
    ADD COLUMN IF NOT EXISTS measured_bmr_kcal NUMERIC(6, 1);
ALTER TABLE body_metrics_logs
    ADD COLUMN IF NOT EXISTS extra JSONB;

ALTER TABLE body_metrics_logs
    DROP CONSTRAINT IF EXISTS ck_metrics_visceral_fat_range;
ALTER TABLE body_metrics_logs
    ADD CONSTRAINT ck_metrics_visceral_fat_range
    CHECK (visceral_fat_level IS NULL OR (visceral_fat_level > 0 AND visceral_fat_level < 60));

ALTER TABLE body_metrics_logs
    DROP CONSTRAINT IF EXISTS ck_metrics_bmr_range;
ALTER TABLE body_metrics_logs
    ADD CONSTRAINT ck_metrics_bmr_range
    CHECK (measured_bmr_kcal IS NULL OR (measured_bmr_kcal > 500 AND measured_bmr_kcal < 5000));

-- Widen ck_metrics_has_data to count the new fields too — DROP+ADD since
-- CHECK constraints can't be altered in place.
ALTER TABLE body_metrics_logs
    DROP CONSTRAINT IF EXISTS ck_metrics_has_data;
ALTER TABLE body_metrics_logs
    ADD CONSTRAINT ck_metrics_has_data
    CHECK (weight_kg IS NOT NULL OR body_fat_percent IS NOT NULL
           OR muscle_mass_kg IS NOT NULL OR visceral_fat_level IS NOT NULL
           OR measured_bmr_kcal IS NOT NULL);
