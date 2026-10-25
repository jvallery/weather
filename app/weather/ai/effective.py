# SPDX-License-Identifier: MIT
"""The automated half of the effective description: what the API, search and the editor show
when no human revision is active.

Today the effective description is the current analysis summary (template-rendered from
validated observations and station data, C18), bound to the current media revision. A
human-edited revision (#44) supersedes it while bound to the current media and evidence;
`weather.video_editorial_state` is that rule's single definition. The API then reports the
description's author as `editor`, never as AI output.
"""

import re
from collections.abc import Mapping
from typing import Any, Final, Literal

LABEL: Final = "AI-generated · sampled-frame analysis"
EDITOR_LABEL: Final = "Editor-written description"


def public_author(author_kind: object) -> Literal["ai", "editor"]:
    """The public author of `analysis_revision.author_kind`: anything but the model is the
    editor, so a human revision is never labelled AI-generated."""
    return "ai" if author_kind == "model" else "editor"


# The provider's model name is untrusted text: only an identifier-shaped name is kept.
MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}")


def public_model(value: object, route: str) -> str:
    """The model name to record and publish: an allowlisted identifier, else the route."""
    return value if isinstance(value, str) and MODEL_NAME.fullmatch(value) else route


def automated_description(row: Mapping[str, Any] | None) -> str | None:
    """The validated AI description of a `public_api.video_analyses` row, or None."""
    if row is None or row.get("revision_state") not in ("complete", "degraded"):
        return None
    summary = row.get("summary")
    return summary if isinstance(summary, str) and summary else None
