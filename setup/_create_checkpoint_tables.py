"""Create the LangGraph PostgresSaver checkpoint tables in Lakebase.

Idempotent — PostgresSaver.setup() runs migrations and is safe to re-run. Called
by setup/01_provision_lakebase.sh so the tables exist before the app ever serves a
request (otherwise the first /api/chat fails with `relation "checkpoints" does not
exist`). Reads connection details from the environment:

    PGHOST, PGPORT, PGDATABASE, PGSSLMODE, DB_USER, PGPASSWORD

DB_USER is the human/email that owns the tables; the app service principal is
granted access by setup/02_grant_app_sp.sh (which runs after this).
"""
import os
import sys

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver

conn = psycopg.connect(
    host=os.environ["PGHOST"],
    port=int(os.environ.get("PGPORT", "5432")),
    dbname=os.environ["PGDATABASE"],
    user=os.environ["DB_USER"],
    password=os.environ["PGPASSWORD"],
    sslmode=os.environ.get("PGSSLMODE", "require"),
)
# CREATE INDEX CONCURRENTLY cannot run inside a transaction.
conn.autocommit = True
PostgresSaver(conn).setup()

tables = [
    r[0]
    for r in conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
    ).fetchall()
]
print(f"    checkpoint tables ready: {tables}", file=sys.stderr)
