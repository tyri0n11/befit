# befit

FastAPI + SQLAlchemy 2.0 (async) + Postgres + Redis. Python 3.13, dependencies
managed by `uv`. Runs locally via Docker Compose; deployment target is k3s.

## Commands

Everything goes through the Makefile — it wires the compose overlays and `--env-file`
for you. `make help` lists all targets.

```
make dev        # build + run the full stack with autoreload (foreground)
make up/down    # same stack, detached / stop
make logs       # tail api logs (SERVICE=postgres to pick another)
make psql       # psql inside the postgres container
make db-init    # re-run scripts/database/*.sql against the running DB
make db-reset   # drop the postgres volume, rebuild schema from scratch
make seed       # load catalog master data from scripts/data/*.yaml
make seed-dry   # validate the seed data, roll the transaction back
make lint       # ruff check + format --check, writes nothing
make fmt        # ruff check --fix + format
make test       # pytest inside the api container
make run        # run the API on the host instead of in a container
```

`make lint` must pass before you consider a change done.

Never invoke `docker compose` directly — the base file alone has no published
ports and no dev overlay, so it will not behave like the dev stack.

## Environment

`.env` is gitignored and auto-created from `.env.example` by any Makefile target
that needs it. Add every new setting to `.env.example` too, or the next clone breaks.

**Postgres is published on host port 5433, not 5432.** This machine runs a native
postgres bound to `127.0.0.1:5432`; a loopback-specific bind beats Docker's
`0.0.0.0:5432`, so `localhost:5432` silently reaches the wrong server and fails with
`role "befit" does not exist`. Inside the compose network the port is still 5432 —
`DB_PORT` only affects the host mapping. SQL clients connect to
`postgresql://befit:befit@localhost:5433/befit`.

## Layout

```
app/
  api/v1/          routers; endpoints/ holds one module per resource
  core/            settings, database — process-wide infrastructure
  models/          SQLAlchemy ORM models, base.py holds Base + BaseModel
  repositories/    data access; the only layer that builds queries
  schemas/         Pydantic request/response models
  services/        business logic; orchestrates repositories
  middleware/
  utils/
scripts/database/  raw SQL schema, numbered per domain
migrations/        (empty — no migration tool wired up yet)
docker/            Dockerfile + compose base/dev/prod
```

Dependency direction is one-way: `api` → `services` → `repositories` → `models`.
Endpoints must not build queries directly, and repositories must not import from
`services`. `core` is imported by everyone and imports nothing from the layers above.

## Conventions

Python:

- Async everywhere for I/O. `async def` endpoints, `AsyncSession`, `asyncpg`.
- Type-annotate everything. SQLAlchemy 2.0 style only: `Mapped[T]` + `mapped_column`,
  never the legacy `Column()` form.
- Modern syntax: `X | None` over `Optional[X]`, `list[X]` over `List[X]`,
  `collections.abc` over `typing` for `AsyncIterator` and friends. Ruff's `UP`
  rules enforce this, so `make fmt` will rewrite the old forms for you.
- Settings fields are `SCREAMING_CASE` (they map to env vars); derived values are
  `@computed_field @property`, not computed at call sites.
- Import order: stdlib, third-party, then `app.*` — separated by blank lines.
  Enforced by ruff isort (`known-first-party = ["app"]`); don't hand-sort.

Ruff is configured in `pyproject.toml`: line length 88, rules `F,E,W,I,UP,B,ASYNC,C4,SIM,RUF`.
`B008` is ignored globally because `Depends()` in a default argument is the FastAPI
idiom, and `F401` is ignored in `__init__.py` for re-exports. Prefer fixing code over
widening these; if a rule genuinely does not fit the project, disable it in
`pyproject.toml` with a comment rather than sprinkling `# noqa`.

SQL (`scripts/database/`):

- **Every script must be idempotent.** `CREATE TABLE/INDEX IF NOT EXISTS`,
  `CREATE OR REPLACE FUNCTION`, `DROP TRIGGER IF EXISTS` before `CREATE TRIGGER`,
  and enums wrapped in `DO $$ ... EXCEPTION WHEN duplicate_object THEN NULL $$`.
  Re-running the whole set with `ON_ERROR_STOP=1` must stay clean.
- Files are numbered by dependency order: `01_init_user` → `02_init_catalog` →
  `03_init_training`. The numbering is load-bearing — Postgres' entrypoint runs
  them alphabetically.
- Name constraints and indexes explicitly: `ck_` checks, `uq_` unique, `idx_` indexes.
- Integer `SERIAL` primary keys. `snake_case` identifiers. `TIMESTAMPTZ`, never
  bare `TIMESTAMP`.
- Section headers use the `-- ---` banner style already in the files.
- Adding a column to an existing table needs an
  `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` at the bottom of the file as well —
  `CREATE TABLE IF NOT EXISTS` skips tables that already exist, so editing the
  `CREATE` alone will not touch a live database. See the backfill block at the end
  of `01_init_user.sql`.

Comments and docs:

- Code, comments and docstrings in English. Long-form docs may be Vietnamese
  (`scripts/database/README.md` is).
- Comment the *why*, not the *what*. Prefer none over restating the code.

## Database access

`app/core/database.py` owns a singleton `Database` — one engine, therefore one
connection pool, per process. Never call `create_async_engine` anywhere else.
`app/core/redis.py` mirrors it for Redis (`cache`, `redis_lifespan`, `get_redis`);
both are entered from the FastAPI lifespan.

- `db_lifespan()` is wired into the FastAPI lifespan; it connects on startup and
  disposes the pool on shutdown.
- Depend on `get_db` in endpoints: `session: AsyncSession = Depends(get_db)`.
- `db.session()` is the transactional scope — commits on success, rolls back on
  exception. Do not commit manually inside a service.
- Touching `db.engine` before `connect()` raises `RuntimeError` on purpose; that
  means something is running outside the lifespan.

## Schema and models

`Base` sets `type_annotation_map = {datetime: DateTime(timezone=True)}`. Without it
SQLAlchemy maps a bare `datetime` annotation to `TIMESTAMP WITHOUT TIME ZONE` while
every SQL column is `TIMESTAMPTZ`, and writing an aware datetime fails with
*"can't subtract offset-naive and offset-aware datetimes"* from asyncpg. Declare
timestamps as plain `Mapped[datetime]` and let the map handle it.

Postgres owns the enum types, so models must not emit `CREATE TYPE`. Use the
`pg_enum()` helper in `app/models/base.py`: it sets `create_type=False` and
`values_callable` so the label (`active`) is stored rather than the member name
(`ACTIVE`).

`BaseModel` (`app/models/base.py`) is abstract and supplies only `created_at` and
`updated_at`. It deliberately carries **no `id`** — each model declares its own
primary key so it can match the `SERIAL` columns in the SQL scripts.

`updated_at` has no database trigger; it relies on SQLAlchemy's
`onupdate=func.now()`. Raw SQL `UPDATE`s therefore will not refresh it.

The SQL scripts are the source of truth for schema right now. When you change a
table, update the script *and* the model, then `make db-init`. There is no Alembic
yet, so nothing checks that the two agree — keep them in sync by hand.

The "at least one primary muscle per exercise" rule is enforced in the service/seed
layer, not the database (see the note atop `02_init_catalog.sql`). Every write path
to `exercise_muscles` must go through that validation.

## Catalog API

Read-only master data: `GET /api/v1/exercises`, `/exercises/{slug}`,
`/muscle-groups`, `/muscle-groups/tree`. **No authentication** — the catalog is
reference data with no user scoping. Gate it by adding `CurrentUser` to the
endpoints if that changes.

There is deliberately no write path: `scripts/seed.py` owns the data, so an
endpoint that inserted an exercise would be overwritten by the next `make seed`
and could also bypass the ≥1-primary-muscle rule.

- `/exercises` filters on `q` (name or slug, `ILIKE`), `pattern`, `equipment`,
  `force`, `muscle` (a muscle group *code*), `role`, `is_unilateral`,
  `requires_overhead`, with `limit`/`offset` and a `total`. The enum filters are
  the model enums, so an unknown value is a 422 rather than an empty page.
- The muscle filter is an `EXISTS` subquery, not a join. An exercise maps to
  several muscles, so a join multiplies its rows and both `total` and `limit`
  come out wrong. `tests/test_catalog.py` pins this.
- `MuscleGroup` has no `parent`/`children` relationships. The tree is three
  levels and a few dozen rows, so `CatalogService.muscle_group_tree` assembles it
  in Python from one flat query ordered by `depth` — a self-referencing lazy
  relationship would emit a load per node and raise under asyncio.

### Caching

Every catalog response is read through a Redis cache (`app/core/cache.py`,
`CATALOG_CACHE_TTL_SECONDS`, default 1h). It is safe precisely *because* the
catalog is read-only: nothing but a seed run can change an answer, so entries
only expire — there is no per-write invalidation to get wrong.

- What is cached is Pydantic JSON, never ORM objects, which is why
  `CatalogService` returns schemas rather than models. Endpoints must not
  `model_validate` the result again.
- `scripts/seed.py` drops the whole `catalog:v1` namespace after a successful
  run. A `--dry-run` returns before that point; without it a rolled-back seed
  would evict a still-correct cache.
- A Redis outage is a cache miss, not a 500 — `JsonCache` logs and falls through
  to Postgres. Same for an entry whose shape no longer validates after a deploy.
- List keys hash *every* filter field plus `limit`/`offset`, so two filter sets
  can never share an entry. Adding a field to `ExerciseFilters` is picked up
  automatically; reordering the dataclass changes every key, which is harmless
  (they just miss once).
- Set `CATALOG_CACHE_TTL_SECONDS=0` to bypass the cache entirely.

## Workout sessions

`/api/v1/sessions` — the logging API. Everything requires `CurrentUser` and is
scoped to that user; `app/services/training.py`.

- A session that belongs to someone else is a **404, not a 403**. Ownership is
  part of the query (`TrainingRepository` joins back to `workout_sessions` for
  nested rows), so it cannot be forgotten by a caller, and ids stay unprobeable.
- `order_index` and `set_index` default to the next free slot; passing one that
  is taken is a 409 rather than an `IntegrityError` from `uq_session_order` /
  `uq_set_index`.
- `POST /sessions` accepts nested `exercises`, so a whole planned day is one
  call. Unknown `exercise_id`s are reported together in a single 422, the way
  `scripts/seed.py` reports validation failures.
- **Tonnage is never stored** — `SessionExercise.tonnage` computes it, doubling
  the weight for a unilateral movement because the logged value is per side.
  This mirrors the header comment of `03_init_training.sql`.
- New rows set `exercises=[]` / `sets=[]` explicitly. Once a flush makes a row
  persistent, an untouched collection counts as *unloaded*, and serialising the
  response would emit a lazy load — `MissingGreenlet` under asyncio. For the
  same reason `_new_session_exercise` assigns the `Exercise` object, not the id.
- `get_session` re-reads with `populate_existing=True`. Without it a session
  already in the identity map keeps the collections it had, so a set deleted
  through its own endpoint would still appear under the session.
- `update_session` re-reads after flushing because `updated_at` is an `onupdate`
  server call and the flush leaves it expired.
- Pydantic mirrors the SQL CHECKs (rpe 5–10, bodyweight 20–300, reps ≥ 1,
  `target_reps_min <= target_reps_max`), so a bad payload is a 422 instead of a
  500 from Postgres. Keep the two in sync when a constraint changes.

## Seeding

`scripts/data/*.yaml` is the source of truth for catalog master data; `scripts/seed.py`
loads it. Edit the YAML, never `INSERT` master data by hand.

- Run as a module from the project root — `uv run python -m scripts.seed` — so that
  `app` resolves. `python scripts/seed.py` puts `scripts/` on `sys.path` instead and
  fails to import `app`.
- Upserts key on the natural keys (`muscle_groups.code`, `exercises.slug`), so
  re-running is safe and YAML edits propagate to existing rows.
- Both `exercise_muscles` and `muscle_groups` are synced declaratively: rows absent
  from the YAML are pruned. Removing a muscle from an exercise removes the link;
  removing a node from the tree deletes it. Muscle groups are pruned deepest-first
  and after link pruning, because `parent_id` and `exercise_muscles.muscle_group_id`
  are both `ON DELETE RESTRICT` — a node an exercise still maps to raises a
  `ForeignKeyViolation` instead of being silently kept.
- `muscle_groups.yaml` is a three-level tree; nesting maps to `parent_id` and `depth`
  is derived from the nesting level, capped at 2 by `ck_depth_range`:

  ```
  depth 0  region   upper_body / lower_body / core
  depth 1  group    chest, back, hips, thighs, abdominals, ...
  depth 2  leaf     chest_upper, lats, quads, abs, ...
  ```

  Only leaves are trackable; regions and groups must set `is_trackable: false`
  because `trg_exercise_muscles_leaf_only` rejects mapping an exercise to them.
  Leaf codes are referenced by `exercises.yaml` — renaming one means updating every
  exercise that maps to it.
- Validation runs before any write and reports all failures at once: slug format,
  unknown or non-trackable muscle codes, duplicate codes, a muscle listed under two
  roles, and the ≥1-primary rule.
- `pattern`, `equipment` and `force` are validated against `pg_enum`, so adding a
  variant to `movement_pattern` / `equipment_type` / `force_type` in SQL needs no
  Python change. `muscle_role` is the exception: `ROLES` in `scripts/seed.py` is the
  set of YAML keys the parser accepts, so it must exist before a connection is open.
  The script cross-checks `ROLES` against the live enum and aborts if they diverge —
  adding a `muscle_role` variant in SQL therefore requires updating `ROLES` too.
- `--dry-run` executes the full write path inside a transaction and rolls it back;
  `--validate-only` never opens a connection.
- CLI scripts pass `db_lifespan(echo=False)`. Without it SQLAlchemy's dev-mode
  `echo` drowns the output in generated SQL.

## Authentication

Local email/password only. `POST /api/v1/auth/{register,login,refresh}` and
`GET /api/v1/auth/me`. Depend on `CurrentUser` (`app/api/v1/dependencies.py`) to
require a caller.

- `SECRET_KEY` must be at least 32 chars — RFC 7518 §3.2's floor for HMAC-SHA256,
  and PyJWT warns below it. `Settings` enforces it and the compose file uses
  `${SECRET_KEY:?...}` so a missing value fails loudly instead of booting on a
  shared default. Generate with `openssl rand -hex 32`.
- Access and refresh tokens carry a `type` claim, and `decode_token` requires the
  expected one. This is what stops a long-lived refresh token being replayed as a
  bearer token.
- Login returns the same error for an unknown email and a wrong password, and
  `AuthErrorCode.INVALID_TOKEN` always renders the same generic message. Keep it
  that way: distinct errors let a caller enumerate registered accounts.
- Passwords are capped at 72 bytes because bcrypt silently truncates there —
  `hash_password` raises and the schema rejects it rather than letting a long
  password become equivalent to its prefix.
- Suspended accounts are rejected *after* the password check, so account status is
  not probeable without valid credentials.
- Tokens are stateless, so `users.token_version` is the only revocation lever.
  Every JWT carries the version it was signed with (`ver`) and `_active_user_for`
  refuses a mismatch. Bump the column to invalidate every outstanding token for a
  user — a password reset does exactly this. There is still no per-device logout.
### Google OAuth

Authorization code flow, server-side: `GET /auth/google/login` (307 to Google) →
`GET /auth/google/callback` → our own `TokenPair`. `app/services/google_oauth.py`.

Two invariants that are easy to break:

- **`state` is single-use.** It lives in Redis and is *deleted* when consumed, so a
  captured callback URL cannot be replayed. A signed stateless value would not do:
  verification is repeatable, consumption is not. PKCE (`S256`) is sent too, and the
  verifier is stored alongside the state.
- **`id_token` is verified, never trusted.** Signature checked against Google's
  JWKS, `aud` must equal `GOOGLE_CLIENT_ID`, `iss` must be Google. Dropping the
  `aud` check would let an id_token minted for a *different* app log someone in.

Account linking: a Google identity whose email matches an existing local account is
linked to it — a second `user_auth` row, so both login methods work
(`uq_user_provider` allows one row per provider per user). **This only happens when
Google asserts `email_verified`**; otherwise anyone could register a Google account
claiming someone else's address and take over their local account. Linking or
creating via Google also sets `users.email_verified`, since Google has proved it.

`GOOGLE_REDIRECT_URI` must match the console registration exactly. A mismatch shows
up as `redirect_uri_mismatch` in the token-exchange error log, not at redirect time.
With the credentials empty, `/auth/google/login` returns 503 rather than failing
obscurely.

The callback returns tokens as JSON. A browser-facing frontend will want a redirect
carrying them instead — that is a deliberate omission, not an oversight.

### Password reset

`POST /auth/password-reset/request` (always 202) → email →
`POST /auth/password-reset/confirm` (204).

- Tokens live in `password_reset_tokens`, and **only their SHA-256 is stored**. A
  plain digest is right here: the token is 32 bytes of `secrets.token_urlsafe`
  entropy, so unlike a password there is nothing for a slow KDF to protect.
- Single-use. `used_at` is stamped on confirm, and requesting a new link spends any
  outstanding one, so only the newest link works.
- Confirming bumps `token_version`, which revokes existing access and refresh
  tokens. Without this a stolen refresh token would survive the victim's reset,
  which defeats the point of resetting.
- `request` returns 202 for unknown addresses, google-only accounts, and inactive
  users alike, and a Resend delivery failure is logged rather than raised. Any of
  those turning into a different response would enumerate accounts.

### Email

`app/services/email.py`. Resend over its **HTTP API**, not SMTP: k3s egress on
25/587 is commonly blocked or provider-rate-limited, and an HTTP call is far easier
to debug from a container.

Do **not** switch to the official `resend` package — it is synchronous only, so it
would block the event loop. `httpx.AsyncClient` posts the same REST payload the SDK
wraps. If the SDK is ever needed, wrap it in `anyio.to_thread.run_sync`.

**Password reset is the only email the app sends.** `register` sends nothing and
`users.email_verified` is never set to true — email verification is not implemented,
so that column is currently decorative.

Sends are logged at INFO on success and ERROR on rejection. Both matter: the endpoint
returns 202 either way, so the log is the only place a delivery failure or success is
visible. `app/core/logging.py` attaches the handler — uvicorn configures only its own
loggers and leaves root at WARNING, so without it every `logger.info()` in
application code is dropped and a send looks like it never happened.

The sending domain must be verified in Resend, and it is the *exact* domain that
counts: `support.tyr1on.io.vn` being verified does not make `tyr1on.io.vn` work. An
unverified sender returns 403, which only shows up in the log.

`EMAIL_FROM` is the complete From value including the display name. Do not wrap it
again in code — `Name <<addr>>` is rejected.

With `RESEND_API_KEY` empty the console backend logs the link instead of sending,
so local dev needs no credentials and no network; `get_email_sender` logs an error
if the key is missing outside development. Tests override the `get_email_sender`
dependency with `RecordingEmailSender` — the suite must never reach the real API.

## Testing

`make test` runs pytest in the api container; `uv run pytest` runs it on the host.
Either way a live database is required.

Every test runs inside a transaction that `tests/conftest.py` rolls back, so the
suite writes nothing. It overrides the `get_db` dependency with a session bound to
that outer transaction, which is why services must flush rather than commit — a
commit in the service layer would defeat this and leak rows into the dev database.

`asyncio_default_test_loop_scope` and `..._fixture_loop_scope` are both `session`
in `pyproject.toml`. asyncpg connections are pinned to the loop that opened them,
so a per-test loop breaks the shared engine pool with *"attached to a different
loop"*.

## Open decisions

Not settled yet — ask before assuming:

- `core/settings.py` uses `lru_cache` for its singleton while `core/database.py`
  uses a `__new__` singleton. Two idioms for the same job; unify eventually.
- Ruff runs only via `make lint`; there is no pre-commit hook or CI gate enforcing it.
- No migration tool. `migrations/` is empty and `scripts/database/` does the work,
  which cannot express destructive changes.
- `app/services/healthcheck.py` is still an empty placeholder.
- CORS in `app/main.py` is wide open (`allow_origins=["*"]`) — fine for local dev,
  must be narrowed before any deployment.
