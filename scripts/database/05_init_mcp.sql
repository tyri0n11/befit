-- =============================================================
-- 05_init_mcp.sql — MCP OAuth clients
-- Run order: 5th (no dependency on other domains)
-- Idempotent: safe to re-run
--
-- Dynamic Client Registration (RFC 7591) record for MCP clients (Claude
-- Desktop, mcp-remote, ...). Authorization codes and the pending-login
-- request live in Redis instead — short-lived and single-use, so losing one
-- just means a retry. Registered clients are meant to keep working across
-- every reconnect, and production Redis is cache-only with no volume, so
-- this table is the durable side of the flow. See app/mcp/oauth.py.
--
-- client_secret is stored in plain text on purpose: the SDK's built-in
-- ClientAuthenticator compares it directly against what get_client() returns,
-- so a hash wouldn't be usable without patching the SDK. It is also
-- secrets.token_hex(32) — high-entropy and machine-only, not a human
-- password, so there is nothing for a slow KDF to protect (the same reasoning
-- password_reset_tokens in 01_init_user.sql already applies to its own token).
-- =============================================================

-- -------------------------------------------------------------
-- oauth_clients — one row per registered MCP client
-- -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id                   TEXT PRIMARY KEY,
    client_secret                TEXT,
    redirect_uris                 JSONB NOT NULL,
    grant_types                    JSONB NOT NULL,
    token_endpoint_auth_method      TEXT,
    scope                            TEXT,
    client_name                       TEXT,
    created_at                         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                          TIMESTAMPTZ NOT NULL DEFAULT now()
);
