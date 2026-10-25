# SPDX-License-Identifier: MIT
"""`object_event` gains `gc_kept` (#19 follow-up). Expand-only; inline DDL.

GC commits a `gc_deleted` row before it deletes, then re-checks under the object key lock. When
that re-check keeps the bytes (a publish registered an intent in between), it now appends
`gc_kept`, so the latest GC event for the key tells whether the bytes went, and the key can be
collected again later.
"""

from alembic import op

revision = "0011_gc_kept"
down_revision = "0010_cleanup"
branch_labels = None
depends_on = None

OBJECT_KEY = r"'^[a-z]+/[0-9a-f]{64}\.[a-z0-9]{1,8}$'"


def statements() -> list[str]:
    return [
        "ALTER TABLE weather.object_event DROP CONSTRAINT ck_object_event_action",
        f"""ALTER TABLE weather.object_event ADD CONSTRAINT ck_object_event_action CHECK (
              action IN ('unavailable','expired','gc_deleted','gc_kept','purged')
              AND bucket IN ('weather-media','weather-originals')
              AND object_key ~ {OBJECT_KEY} AND length(reason) > 0 AND length(actor) > 0)""",
    ]


def upgrade() -> None:
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Production downgrade is disabled; use expand/contract or PITR")
