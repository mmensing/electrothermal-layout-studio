# SPDX-License-Identifier: MPL-2.0
"""Document identity is persistence metadata, never scientific configuration."""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from etlayout.models import Project, SimulationError


def document_id(project: Project) -> str:
    value = getattr(project, "_document_id", None)
    if value is None:
        value = str(uuid.uuid4())
        project._document_id = value
    return str(uuid.UUID(value))


def restore_document_id(project: Project, value: str | None, source: Path) -> None:
    """Legacy locations identify an unsaved migration; a saved UUID survives moves."""
    try:
        project._document_id = str(uuid.UUID(value)) if value else str(
            uuid.uuid5(uuid.NAMESPACE_URL, Path(os.path.normcase(str(source.absolute()))).as_uri()))
    except (ValueError, TypeError, AttributeError) as error:
        raise SimulationError("Invalid persisted document identity") from error
