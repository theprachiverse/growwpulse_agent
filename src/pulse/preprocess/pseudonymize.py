"""Pseudonymization helper for reviews (Task 2.3).

Generates consistent, non-reversible pseudonym IDs (rid):
`rid = "rid_" + sha256(f"{source}:{review_id}")[:10]`
"""

from __future__ import annotations

import hashlib

from pulse.models import Source


def generate_rid(source: Source | str, review_id: str) -> str:
    """Generate a deterministic pseudonymous ID for a review and drop original review_id."""
    key = f"{source}:{review_id}".encode()
    digest = hashlib.sha256(key).hexdigest()[:10]
    return f"rid_{digest}"
