"""Logger utility — minimal YAGNI implementation for error logging."""

from __future__ import annotations

import logging
import uuid


def log_error(message: str | None) -> str | None:
    """Log an error message and return a unique error identifier.

    Args:
        message: The error message, or empty string, or None.

    Returns:
        A non-empty error identifier for valid messages, or None for
        empty/None inputs.
    """
    if not message:
        return None
    error_id = str(uuid.uuid4())
    logging.error("%s: %s", error_id, message)
    return error_id
