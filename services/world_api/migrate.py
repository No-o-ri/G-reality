"""Apply numbered SQL migrations from database/migrations to the database in DATABASE_URL_DIRECT.

Usage: .venv\\Scripts\\python -m world_api.migrate [--status]
Each file is self-contained (BEGIN … INSERT INTO schema_migrations … COMMIT), so a failed file
leaves nothing behind. Applied versions are skipped, making reruns safe.
"""

from __future__ import annotations

import sys

import psycopg

from .config import ROOT, get_settings

MIGRATIONS = ROOT / "database" / "migrations"


def applied_versions(conn: psycopg.Connection) -> set[str]:
    exists = conn.execute("SELECT to_regclass('public.schema_migrations') IS NOT NULL").fetchone()[0]
    if not exists:
        return set()
    return {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}


def migrate(url: str, status_only: bool = False) -> list[str]:
    """Returns the versions applied by this call."""
    ran: list[str] = []
    with psycopg.connect(url, autocommit=True) as conn:
        done = applied_versions(conn)
        for path in sorted(MIGRATIONS.glob("*.sql")):
            version = path.stem
            if version in done:
                print(f"  applied   {version}")
                continue
            if status_only:
                print(f"  pending   {version}")
                continue
            conn.execute(path.read_text(encoding="utf-8"))  # multi-statement script, no parameters
            ran.append(version)
            print(f"  APPLIED   {version}")
        info = conn.execute("SELECT current_database(), inet_server_addr() IS NOT NULL").fetchone()
        print(f"database: {info[0]}")
    return ran


def main() -> int:
    settings = get_settings()
    if not settings.database_url_direct:
        print("DATABASE_URL_DIRECT / DATABASE_URL is not set (.env.local)", file=sys.stderr)
        return 1
    migrate(settings.database_url_direct, status_only="--status" in sys.argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
