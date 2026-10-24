# SPDX-License-Identifier: MIT
import json
import runpy
from pathlib import Path

import pytest

HELPERS = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/verify_foundation.py"))


def test_kubectl_list_and_concatenated_documents_have_identical_resources() -> None:
    resources = [
        {"kind": "Namespace", "metadata": {"name": "weather"}},
        {"kind": "Deployment", "metadata": {"name": "api"}},
    ]
    stream = "\n".join(json.dumps(item) for item in resources) + "\n"
    wrapped = json.dumps({"kind": "List", "items": resources})
    assert HELPERS["documents"](stream) == resources
    assert HELPERS["documents"](wrapped) == resources


def test_kubectl_output_does_not_silently_discard_invalid_trailing_data() -> None:
    with pytest.raises(json.JSONDecodeError):
        HELPERS["documents"]('{"kind":"Namespace"}\nnot JSON')
