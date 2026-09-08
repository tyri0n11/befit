-- =============================================================
-- 07_init_calendar.sql — Google Calendar sync
-- Run order: 7th (depends on users + workout_sessions)
-- Idempotent: safe to re-run
--
-- Two-way sync between a user's PLANNED workout sessions and one Google
-- Calendar. `calendar_connections` is one row per user (a user connects
-- exactly one calendar); `session_calendar_events` maps a session to the
-- Google event that represents it, both for push (know whether to
-- insert/patch/delete) and pull (the webhook looks up which session a
-- changed event belongs to). See app/services/google_calendar.py.
-- =============================================================

-- -------------------------------------------------------------
-- calendar_connections — one Google Calendar link per user
-- -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS calendar_connections (
    id                  SERIAL PRIMARY KEY,
    user_id             INTEGER NOT NULL UNIQUE
                        REFERENCES users (id) ON DELETE CASCADE,
    access_token        TEXT NOT NULL,
    refresh_token       TEXT NOT NULL,
    token_expires_at    TIMESTAMPTZ NOT NULL,
    scope               TEXT NOT NULL,
    calendar_id         VARCHAR(255) NOT NULL DEFAULT 'primary',
    -- Google push notification channel used for the pull direction. NULL
    -- until the first watch channel is registered.
    channel_id          UUID,
    resource_id         VARCHAR(255),
    channel_expires_at  TIMESTAMPTZ,
    -- events.list incremental sync cursor; NULL means the next pull must do
    -- a full sync first.
    sync_token          TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- -------------------------------------------------------------
-- session_calendar_events — session <-> Google event mapping
-- -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS session_calendar_events (
    id               SERIAL PRIMARY KEY,
    session_id       INTEGER NOT NULL UNIQUE
                     REFERENCES workout_sessions (id) ON DELETE CASCADE,
    user_id          INTEGER NOT NULL
                     REFERENCES users (id) ON DELETE CASCADE,
    google_event_id  VARCHAR(255) NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_user_google_event UNIQUE (user_id, google_event_id)
);

CREATE INDEX IF NOT EXISTS idx_session_calendar_events_user
    ON session_calendar_events (user_id);
