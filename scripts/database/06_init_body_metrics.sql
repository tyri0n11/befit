-- =============================================================
-- 06_init_body_metrics.sql — Body Metrics Log
-- Run order: 6th (depends on users only)
-- Idempotent: safe to re-run
--
-- Tracks weight/body-fat/muscle-mass over time, one row per user per day.
-- This is a log, not a snapshot — unlike user_profiles.weight_kg (the
-- current value BMR/TDEE is computed from), a row here is never overwritten
-- by a later day's entry, only by a same-day re-log (see uq_metrics_user_date
-- and the upsert-by-date behavior in app/services/body_metrics.py).
-- =============================================================

-- -------------------------------------------------------------
-- body_metrics_logs — one row per user per day
-- -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS body_metrics_logs (
    id                SERIAL PRIMARY KEY,
    user_id           INTEGER NOT NULL
                      REFERENCES users (id) ON DELETE CASCADE,
    measured_at       DATE NOT NULL DEFAULT CURRENT_DATE,
    weight_kg         NUMERIC(5, 2),
    body_fat_percent  NUMERIC(4, 1),
    muscle_mass_kg    NUMERIC(5, 2),
    notes             TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_metrics_weight_range
        CHECK (weight_kg IS NULL OR (weight_kg > 20 AND weight_kg < 300)),
    CONSTRAINT ck_metrics_fat_range
        CHECK (body_fat_percent IS NULL OR (body_fat_percent >= 0 AND body_fat_percent <= 70)),
    CONSTRAINT ck_metrics_muscle_range
        CHECK (muscle_mass_kg IS NULL OR (muscle_mass_kg > 0 AND muscle_mass_kg < 200)),
    -- A row that carries none of the three metrics isn't a log entry.
    CONSTRAINT ck_metrics_has_data
        CHECK (weight_kg IS NOT NULL OR body_fat_percent IS NOT NULL OR muscle_mass_kg IS NOT NULL),
    -- One entry per day; logging again the same day updates it in place
    -- (see get_or_create_for_date in app/repositories/body_metrics.py).
    CONSTRAINT uq_metrics_user_date
        UNIQUE (user_id, measured_at)
);

CREATE INDEX IF NOT EXISTS idx_body_metrics_user_date
    ON body_metrics_logs (user_id, measured_at DESC);
