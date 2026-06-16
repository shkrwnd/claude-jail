"""Audit event submission with retry logic."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

from sdk.auth import get_api_url, get_headers
from sdk.models import AuditEvent

logger = logging.getLogger("outhora.audit")

MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 0.5  # seconds


def send_audit_event(event: AuditEvent, retries: int = MAX_RETRIES) -> bool:
    """Send an audit event to Outhora with retry logic.

    Returns True if the event was accepted, False otherwise.
    Never raises — audit failures should not block tool execution.
    """
    url = f"{get_api_url()}/api/v1/audit"
    headers = get_headers()
    body = json.dumps(event.to_dict()).encode()

    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status in (200, 201, 202):
                    return True
                logger.warning("Audit API returned status %d", resp.status)
        except urllib.error.HTTPError as e:
            logger.warning("Audit HTTP error (attempt %d/%d): %s", attempt + 1, retries, e)
        except (urllib.error.URLError, OSError) as e:
            logger.warning("Audit network error (attempt %d/%d): %s", attempt + 1, retries, e)

        if attempt < retries - 1:
            backoff = RETRY_BACKOFF_BASE * (2 ** attempt)
            time.sleep(backoff)

    logger.error("Failed to send audit event after %d attempts", retries)
    return False
