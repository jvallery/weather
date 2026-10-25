# SPDX-License-Identifier: MIT
"""The one effective-description function: what the API, search and (later) the editor show.

Today the effective description is the current validated analysis summary, bound to the
current media revision. The editor (#44) extends this with human-edited revisions.
"""

from collections.abc import Mapping
from typing import Any, Final

LABEL: Final = "AI-generated · sampled-frame analysis"


def effective_description(row: Mapping[str, Any] | None) -> str | None:
    """The public description of a `public_api.video_analyses` row, or None."""
    if row is None or row.get("revision_state") not in ("complete", "degraded"):
        return None
    summary = row.get("summary")
    return summary if isinstance(summary, str) and summary else None
