# SPDX-License-Identifier: MIT
"""Initial retained schema, safe public projections and role boundaries."""

from alembic import op
from sqlalchemy import text
from weather.db.ddl_v1 import statements
from weather.db.schema_v1 import build_metadata

revision = "0001_schema"
down_revision = "0000_namespace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    metadata = build_metadata()
    metadata.create_all(
        op.get_bind(),
        tables=[
            table
            for table in metadata.sorted_tables
            if table.name not in {"station", "device"}
        ],
        checkfirst=False,
    )
    for statement in statements([table.name for table in metadata.sorted_tables]):
        op.execute(text(statement))


def downgrade() -> None:
    raise RuntimeError("Production downgrade is disabled; use expand/contract or PITR")
