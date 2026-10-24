# Migrations

`0001_baseline` is the slice-1 schema as inline DDL (#74). Applied revisions are
frozen: add a new expand/contract revision with its own inline DDL and update
`weather.db.models.metadata` in the same commit, so `alembic check` stays clean.
Never import application modules into a revision.

Production entrypoint: `python -m weather.db.migrate`, which waits boundedly for
the first connection and the managed roles, then execs `alembic upgrade head`.
Downgrades are disabled. See `docs/operations/database.md`.
