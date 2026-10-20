# SPDX-License-Identifier: MPL-2.0
"""Deliver typed worker errors without retaining their execution frames."""
from __future__ import annotations


def detached_error(error: Exception) -> Exception:
    """Keep the existing exception contract, but release worker/frame ownership.

    A traceback can retain ``run`` and its QThread after native Qt deletion.
    Chained exceptions retain the same graph indirectly. GUI consumers use the
    error type, message and explicit attributes, so those remain unchanged.
    This does not identify the cause of an intermittent native GC fault.
    """
    error.__traceback__ = None
    error.__cause__ = None
    error.__context__ = None
    return error
