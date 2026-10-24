# SPDX-License-Identifier: MIT
"""Add source-time current pointers and a commit-serialized export boundary."""

from alembic import op
from weather.db.ddl_v3 import lock_statement, statements
from weather.db.schema_v1 import build_metadata
from weather.db.schema_v3 import TABLE_NAMES, extend_metadata
from weather.telemetry.schema_v2 import extend_metadata as extend_telemetry

revision = "0003_current_publication"
down_revision = "0002_telemetry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(lock_statement())
    metadata = build_metadata()
    extend_telemetry(metadata)
    extend_metadata(metadata)
    bind = op.get_bind()
    for name in TABLE_NAMES:
        metadata.tables[f"weather.{name}"].create(bind)
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Current/publication downgrade is unsupported; use expand/contract or PITR")
