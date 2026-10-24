# SPDX-License-Identifier: MIT
"""Current SQLAlchemy metadata. Add later schema revisions here through migrations."""

from weather.db.schema_v1 import build_metadata

metadata = build_metadata()
