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
    weight_kg      NUMERIC(5, 2),
    goal           training_goal,
    activity_level SMALLINT,
    tdee_kcal      NUMERIC(6, 1),
    bmr_kcal       NUMERIC(6, 1),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_height_range
        CHECK (height_cm IS NULL OR (height_cm > 80 AND height_cm < 260)),
    CONSTRAINT ck_profile_weight_range
        CHECK (weight_kg IS NULL OR (weight_kg > 20 AND weight_kg < 300)),
    CONSTRAINT ck_activity_range
        CHECK (activity_level IS NULL OR activity_level BETWEEN 1 AND 5),
    CONSTRAINT ck_birth_past
        CHECK (birth_date IS NULL OR birth_date < CURRENT_DATE)
);

-- -------------------------------------------------------------
-- password_reset_tokens — single-use, short-lived
--
-- Only the SHA-256 of the token is stored. A leaked dump therefore
-- cannot be used to reset anyone's password. The token itself is
-- high-entropy random, so a plain digest is enough — bcrypt would
-- only add cost without adding security here.
-- -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER NOT NULL
               REFERENCES users (id) ON DELETE CASCADE,
    token_hash CHAR(64) NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at    TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_reset_expires_after_created
        CHECK (expires_at > created_at)
);

CREATE INDEX IF NOT EXISTS idx_password_reset_user
    ON password_reset_tokens (user_id);

-- -------------------------------------------------------------
-- Timestamp backfill — patches databases created before every
-- table carried both created_at and updated_at. No-op on a fresh
-- database, where the columns above already exist.
-- -------------------------------------------------------------

-- Bumped whenever credentials change; JWTs carry the value they were
-- issued with, so raising it invalidates every outstanding access and
-- refresh token for that user. This is the only revocation mechanism —
-- the tokens themselves are stateless.
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0;

ALTER TABLE user_auth
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

ALTER TABLE user_profiles
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();

-- weight_kg backs the BMR/TDEE calculation (Mifflin-St Jeor needs it
-- alongside sex/birth_date/height_cm) — the original table predates that
-- feature, so the column and its check arrive as a backfill.
ALTER TABLE user_profiles
    ADD COLUMN IF NOT EXISTS weight_kg NUMERIC(5, 2);

DO $$ BEGIN
    ALTER TABLE user_profiles
        ADD CONSTRAINT ck_profile_weight_range
        CHECK (weight_kg IS NULL OR (weight_kg > 20 AND weight_kg < 300));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;