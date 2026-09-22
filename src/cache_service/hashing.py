"""Content digests used as cache keys."""

import hashlib
import json
from collections.abc import Sequence


def digest_text(value: str) -> str:
    """Digest of a single transformer input."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def digest_lists(list_1: Sequence[str], list_2: Sequence[str]) -> str:
    """Digest identifying a payload request.

    JSON encoding is unambiguous: it keeps element order (which is part of the
    payload's identity) and prevents strings containing separators from
    colliding with different list splits.
    """
    canonical = json.dumps([list(list_1), list(list_2)], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
