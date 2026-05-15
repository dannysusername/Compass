#!/usr/bin/env python
"""Idempotent additive schema migrator — run by Heroku's release phase
(see the Procfile `release:` line) BEFORE the new dynos take traffic.

A non-zero exit aborts the Heroku release: the build that needs a column
the DB doesn't have never reaches users — the previous release keeps
serving. This closes the foot-gun the auto-deploy pipeline would
otherwise create (main.py's PRAGMA migrations are SQLite-only; on
Postgres the app code just *assumes* the schema already matches).

Engine-agnostic and metadata-driven: for every table SQLModel knows
about that already exists in the DB, ALTER in any model column the DB is
missing, compiling the column's type with the live engine's dialect (so
a bool is BOOLEAN on Postgres, NUMERIC on SQLite — always correct).
Brand-new tables are handled by create_all.

Scope is deliberately the *additive* change class that has bitten prod
repeatedly (a new nullable / defaulted column on an existing table). It
does NOT do type changes, drops, renames, or constraint edits — those
stay manual and must be applied to the prod DB by hand before the deploy
that needs them (see CLAUDE.md "CI/CD + release-phase migrations").
"""
import logging
import sys

from sqlalchemy import inspect
from sqlmodel import SQLModel

# Importing main wires up DATABASE_URL resolution + the engine + every
# table class exactly as the app sees them. It does NOT start the server
# (lifespan only runs under uvicorn), so this is a cheap, faithful import.
import main
from main import engine

log = logging.getLogger("compass.migrate")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def _render_default(column):
    """SQL literal for a scalar Python-side default, or None.

    ADD COLUMN NOT NULL on a populated table needs a DB-level DEFAULT —
    SQLModel's Field(default=...) is Python-side only, so we re-emit it
    into the DDL. Non-scalar defaults (default_factory, callables) return
    None and the column is added nullable instead of aborting the deploy.
    """
    d = column.default
    if d is None or not getattr(d, "is_scalar", False):
        return None
    val = d.arg
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, str):
        return "'" + val.replace("'", "''") + "'"
    return None


def run() -> int:
    # 1. Brand-new tables on both engines. create_all never drops or
    #    alters an existing table, so this is safe on a populated prod DB.
    SQLModel.metadata.create_all(engine)

    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    dialect = engine.dialect
    added = 0

    with engine.begin() as conn:
        for table_name, table in SQLModel.metadata.tables.items():
            if table_name not in existing_tables:
                continue  # create_all just made it; nothing to backfill
            db_cols = {c["name"] for c in insp.get_columns(table_name)}
            for col in table.columns:
                if col.name in db_cols:
                    continue
                coltype = col.type.compile(dialect=dialect)
                default_sql = _render_default(col)
                if not col.nullable and default_sql is not None:
                    tail = f"{coltype} NOT NULL DEFAULT {default_sql}"
                elif default_sql is not None:
                    tail = f"{coltype} DEFAULT {default_sql}"
                else:
                    # No safe default → add nullable even if the model
                    # says NOT NULL; aborting the whole release over a
                    # backfillable column would be worse.
                    tail = coltype
                stmt = f'ALTER TABLE "{table_name}" ADD COLUMN "{col.name}" {tail}'
                log.info("migrating: %s", stmt)
                conn.exec_driver_sql(stmt)
                added += 1

    log.info("additive migration complete — %d column(s) added", added)
    return added


if __name__ == "__main__":
    try:
        run()
    except Exception:
        log.exception("MIGRATION FAILED — aborting release (old release keeps serving)")
        sys.exit(1)
