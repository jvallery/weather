# SPDX-License-Identifier: MIT
"""Current SQLAlchemy metadata. Add later schema revisions here through migrations."""

from weather.db.schema_v1 import build_metadata
from weather.db.schema_v3 import extend_metadata as extend_current
from weather.db.schema_v4 import extend_metadata as extend_evidence
from weather.db.schema_v5 import extend_metadata as extend_aggregates
from weather.telemetry.schema_v2 import extend_metadata as extend_telemetry

metadata = build_metadata()
extend_telemetry(metadata)
extend_current(metadata)
extend_evidence(metadata)
extend_aggregates(metadata)
