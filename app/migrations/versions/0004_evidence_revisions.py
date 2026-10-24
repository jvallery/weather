# SPDX-License-Identifier: MIT
"""Freeze safe public qualification values at a globally ordered evidence watermark."""

from alembic import op
from weather.db.ddl_v4 import lock_statements, statements
from weather.db.schema_v1 import build_metadata
from weather.db.schema_v3 import extend_metadata as extend_publication
from weather.db.schema_v4 import TABLE_NAMES, extend_metadata
from weather.telemetry.schema_v2 import extend_metadata as extend_telemetry

revision = "0004_evidence_revisions"
down_revision = "0003_current_publication"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in lock_statements():
        op.execute(statement)
    metadata = build_metadata()
    extend_telemetry(metadata)
    extend_publication(metadata)
    extend_metadata(metadata)
    bind = op.get_bind()
    for name in TABLE_NAMES:
        metadata.tables[f"weather.{name}"].create(bind)
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Public evidence history is retained; use expand/contract or PITR")
