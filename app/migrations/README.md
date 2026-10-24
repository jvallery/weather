# Migrations

Migrations are owned by WX-08 (#9); Alembic is pinned in the backend lock.

`0000_namespace` creates retained station/device identities; `0001_schema`
adds schema v1, constraints, projections, functions and grants. Versioned
snapshots in `weather.db.schema_v1` and `ddl_v1` must remain immutable after
release. Extend current metadata and add a later revision instead.

Production entrypoint: `python -m weather.db.migrate`, which waits boundedly
for managed roles and then execs `alembic upgrade head`. Downgrades are disabled.
See `docs/operations/database.md` for expand/contract and cluster qualification.
