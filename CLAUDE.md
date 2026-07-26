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

- `db_lifespan()` is wired into the FastAPI lifespan; it connects on startup and
  disposes the pool on shutdown.
- Depend on `get_db` in endpoints: `session: AsyncSession = Depends(get_db)`.
- `db.session()` is the transactional scope — commits on success, rolls back on
  exception. Do not commit manually inside a service.
- Touching `db.engine` before `connect()` raises `RuntimeError` on purpose; that
  means something is running outside the lifespan.

## Schema and models

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

## Open decisions

Not settled yet — ask before assuming:

- `core/settings.py` uses `lru_cache` for its singleton while `core/database.py`
  uses a `__new__` singleton. Two idioms for the same job; unify eventually.
- Ruff runs only via `make lint`; there is no pre-commit hook or CI gate enforcing it.
- No migration tool. `migrations/` is empty and `scripts/database/` does the work,
  which cannot express destructive changes.
- `app/api/v1/api.py`, `app/services/healthcheck.py`, `app/utils/security.py` are
  empty placeholders. The router is not mounted in `app/main.py` yet, and
  `settings.API_V1_STR` is unused.
