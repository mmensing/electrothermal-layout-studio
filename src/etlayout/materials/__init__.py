# SPDX-License-Identifier: MPL-2.0
"""Persistent material library and validated temperature-dependent properties."""
from .database import MaterialDatabase, reference_materials
from .models import evaluate_property

__all__ = ["MaterialDatabase", "evaluate_property", "reference_materials"]
