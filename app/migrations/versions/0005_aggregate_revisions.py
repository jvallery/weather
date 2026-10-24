# SPDX-License-Identifier: MIT
"""Retain computed aggregate revisions and fenced current pointers."""

from alembic import op
from weather.db.ddl_v5 import statements
from weather.db.schema_v1 import build_metadata
from weather.db.schema_v5 import TABLE_NAMES, extend_metadata

revision = "0005_aggregate_revisions"
down_revision = "0004_evidence_revisions"
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
    raise RuntimeError("Aggregate evidence is retained; use expand/contract or PITR")
