"""Small deterministic helpers with no third-party dependencies."""

from __future__ import annotations

import hashlib
import random


def stable_seed(*parts: object) -> int:
    payload = "|".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def rng_for(*parts: object) -> random.Random:
    return random.Random(stable_seed(*parts))
