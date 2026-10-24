# SPDX-License-Identifier: MIT
"""Bootstrap the namespace and retained station/device identities."""

from alembic import op
from weather.db.schema_v1 import build_metadata

revision = "0000_namespace"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA weather")
    op.execute("CREATE SCHEMA public_api")
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    metadata = build_metadata()
    metadata.create_all(
        op.get_bind(),
        tables=[metadata.tables["weather.station"], metadata.tables["weather.device"]],
        checkfirst=False,
    )


def downgrade() -> None:
    raise RuntimeError("Production downgrade is disabled; use expand/contract or PITR")
