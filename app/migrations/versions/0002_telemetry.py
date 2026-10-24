# SPDX-License-Identifier: MIT
"""Add raw receipt provenance, lean conflicts and transport coverage diagnostics."""

from alembic import op
from weather.db.schema_v1 import build_metadata
from weather.telemetry.ddl_v2 import statements
from weather.telemetry.schema_v2 import TABLE_NAMES, extend_metadata

revision = "0002_telemetry"
down_revision = "0001_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    metadata = build_metadata()
    extend_metadata(metadata)
    bind = op.get_bind()
    for name in TABLE_NAMES:
        metadata.tables[f"weather.{name}"].create(bind)
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Telemetry evidence is retained; schema downgrade is unsupported")
