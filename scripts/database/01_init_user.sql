-- =============================================================
-- 01_users.sql — Users, Auth & Profiles
-- Run order: 1st (training domain depends on users)
-- Idempotent: safe to re-run (IF NOT EXISTS / DROP guards)
-- =============================================================

CREATE EXTENSION IF NOT EXISTS citext;

-- -------------------------------------------------------------
-- Enum types
-- -------------------------------------------------------------

DO $$ BEGIN
    CREATE TYPE user_status AS ENUM ('active', 'suspended', 'deleted');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE auth_provider AS ENUM ('google', 'local');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE sex AS ENUM ('male', 'female');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE training_goal AS ENUM (
        'lose_fat', 'maintain', 'gain_muscle', 'recomp'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- -------------------------------------------------------------
-- users — core identity
-- -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS users (
    id             SERIAL PRIMARY KEY,
    email          CITEXT NOT NULL UNIQUE,
    display_name   VARCHAR(64) NOT NULL,
    status         user_status NOT NULL DEFAULT 'active',
    email_verified BOOLEAN NOT NULL DEFAULT FALSE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_email_format
        CHECK (email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$')
);

CREATE INDEX IF NOT EXISTS idx_users_status ON users (status);

-- -------------------------------------------------------------
-- user_auth — one row per login method
-- -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS user_auth (
    id               SERIAL PRIMARY KEY,
    user_id          INTEGER NOT NULL
                     REFERENCES users (id) ON DELETE CASCADE,
    provider         auth_provider NOT NULL,
    provider_user_id VARCHAR(255),
    password_hash    VARCHAR(255),
    last_login_at    TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_provider_identity
        UNIQUE (provider, provider_user_id),
    CONSTRAINT uq_user_provider
        UNIQUE (user_id, provider),
    CONSTRAINT ck_auth_shape CHECK (
        (provider = 'google'
            AND provider_user_id IS NOT NULL
            AND password_hash IS NULL)
        OR
        (provider = 'local'
            AND password_hash IS NOT NULL
            AND provider_user_id IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_user_auth_user ON user_auth (user_id);

-- -------------------------------------------------------------
-- user_profiles — physical attributes, 1:1 with users
-- -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS user_profiles (
    user_id        INTEGER PRIMARY KEY
                   REFERENCES users (id) ON DELETE CASCADE,
    sex            sex,
    birth_date     DATE,
    height_cm      SMALLINT,
    goal           training_goal,
    activity_level SMALLINT,
    tdee_kcal      NUMERIC(6, 1),
    bmr_kcal       NUMERIC(6, 1),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_height_range
        CHECK (height_cm IS NULL OR (height_cm > 80 AND height_cm < 260)),
    CONSTRAINT ck_activity_range
        CHECK (activity_level IS NULL OR activity_level BETWEEN 1 AND 5),
    CONSTRAINT ck_birth_past
        CHECK (birth_date IS NULL OR birth_date < CURRENT_DATE)
);

-- -------------------------------------------------------------
-- Timestamp backfill — patches databases created before every
-- table carried both created_at and updated_at. No-op on a fresh
-- database, where the columns above already exist.
-- -------------------------------------------------------------

ALTER TABLE user_auth
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

ALTER TABLE user_profiles
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();