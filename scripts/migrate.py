"""Apply scripts/database/*.sql, in order, against the configured database.

Run from the project root so that `app` is importable:

    uv run python -m scripts.migrate

This is what the Argo CD PreSync hook Job runs in dev/prod (see
manifests/befit/base/migrate-job.yaml in the my-k3s-argocd repo) using the
same image the Deployment runs, since the cluster has no inbound path for
SSH/kubeconfig-based tooling to reach in from CI. `make db-init` covers the
same job locally via psql in the compose stack.

Every file under scripts/database/ is idempotent by convention (see
CLAUDE.md), so re-running the full set on every sync is always safe — this
does not track which files already ran.
"""

import asyncio
import sys
from pathlib import Path

import asyncpg

from app.core.settings import settings

SCHEMA_DIR = Path(__file__).parent / "database"


async def main() -> None:
    files = sorted(SCHEMA_DIR.glob("*.sql"))
    if not files:
        print(f"No schema files found in {SCHEMA_DIR}", file=sys.stderr)
        sys.exit(1)

    conn = await asyncpg.connect(
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        database=settings.DB_NAME,
    )
    try:
        for path in files:
            print(f"==> {path.name}")
            # Simple query protocol: runs the whole file, every statement
            # (including DO $$ ... $$ blocks) in one round trip, exactly like
            # `psql -v ON_ERROR_STOP=1 -f`. A failing statement raises and
            # aborts the run rather than leaving a half-applied schema.
            await conn.execute(path.read_text())
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
