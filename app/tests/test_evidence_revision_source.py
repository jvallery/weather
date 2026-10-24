# SPDX-License-Identifier: MIT
"""Bounded source checks for the retained public evidence authority boundary."""

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable
from weather.db.ddl_v4 import backfill_statement, lock_statements, statements
from weather.db.schema_v1 import build_metadata
from weather.db.schema_v3 import PUBLIC_FAMILIES
from weather.db.schema_v3 import extend_metadata as extend_publication
from weather.db.schema_v4 import extend_metadata
from weather.telemetry.schema_v2 import extend_metadata as extend_telemetry


def test_revision_schema_retains_only_the_safe_public_projection_and_order() -> None:
    metadata = build_metadata()
    extend_telemetry(metadata)
    extend_publication(metadata)
    extend_metadata(metadata)
    table = metadata.tables["weather.telemetry_evidence_revision"]
    assert set(table.c.keys()) == {
        "revision_seq",
        "publication_seq",
        "id",
        "station_alias",
        "device_alias",
        "message_type",
        "observed_at",
        "received_at",
        "row_idx",
        "values",
        "field_quality",
        "quality",
    }
    ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert "revision_seq >= publication_seq" in ddl and "ON DELETE RESTRICT" in ddl
    assert len({index.name for index in table.indexes}) == 2


def test_public_evidence_authority_is_select_only_and_internal_functions_are_revoked() -> None:
    ddl = statements()
    grants = [sql for sql in ddl if sql.startswith("GRANT")]
    assert grants == [
        "GRANT SELECT ON public_api.telemetry_evidence_revisions TO weather_api,weather_worker"
    ]
    assert any("WITH(security_barrier=true)" in sql for sql in ddl)
    assert any(
        sql.startswith("REVOKE ALL ON weather.telemetry_evidence_revision ")
        and "FROM PUBLIC,weather_api,weather_worker,weather_ingest,weather_editor" in sql
        for sql in ddl
    )
    assert any(
        sql.startswith("REVOKE ALL ON FUNCTION weather.append_telemetry_evidence")
        and "weather.record_telemetry_evidence_quality()" in sql
        and "FROM PUBLIC,weather_api,weather_worker,weather_ingest,weather_editor" in sql
        for sql in ddl
    )


def test_backfill_uses_original_safe_positions_without_clock_or_outbox_writes() -> None:
    sql = backfill_statement()
    assert "FROM public_api.telemetry_publications" in sql
    assert "SELECT publication_seq,publication_seq," in sql
    assert "outbox" not in sql and "UPDATE" not in sql
    locks = lock_statements()
    assert locks[0].index("weather.telemetry_receipt") < locks[0].index("weather.obs_st")
    assert "FOR UPDATE" in locks[-1]


def test_insert_trigger_runs_after_v3_publication_on_only_public_families() -> None:
    assert "telemetry_revision_published" > "telemetry_published"
    inserts = [
        sql for sql in statements() if sql.startswith("CREATE TRIGGER telemetry_revision_published")
    ]
    assert len(inserts) == len(PUBLIC_FAMILIES)
    assert all(any(f"ON weather.{family} " in sql for family in PUBLIC_FAMILIES) for sql in inserts)
