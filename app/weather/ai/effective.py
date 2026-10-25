# SPDX-License-Identifier: MIT
"""The one effective-description function: what the API, search and (later) the editor show.

Today the effective description is the current analysis summary (template-rendered from
validated observations and station data, C18), bound to the current media revision. The editor
(#44) writes `operator` revisions, which the API labels as the editor's, never as AI output.
"""

import re
from collections.abc import Mapping
from typing import Any, Final, Literal

LABEL: Final = "AI-generated · sampled-frame analysis"
EDITOR_LABEL: Final = "Editor-written description"
# No hour passed visual review: the text is the station's measurements only, never AI-labelled.
STATION_LABEL: Final = "Station measurements only"


def public_author(author_kind: object) -> Literal["ai", "editor"]:
    """The public author of `analysis_revision.author_kind`: anything but the model is the
    editor, so a human revision is never labelled AI-generated."""
    return "ai" if author_kind == "model" else "editor"
# The provider's model name is untrusted text: only an identifier-shaped name is kept.
MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}")


def public_model(value: object, route: str) -> str:
    """The model name to record and publish: an allowlisted identifier, else the route."""
    return value if isinstance(value, str) and MODEL_NAME.fullmatch(value) else route


def effective_description(row: Mapping[str, Any] | None) -> str | None:
    """The public description of a `public_api.video_analyses` row, or None."""
    if row is None or row.get("revision_state") not in ("complete", "degraded"):
        return None
    summary = row.get("summary")
    return summary if isinstance(summary, str) and summary else None
