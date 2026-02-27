from __future__ import annotations

import hashlib
import random


RANKS = "23456789TJQKA"
SUITS = "cdhs"


def build_shuffled_deck(seed: int) -> list[str]:
    deck = [f"{rank}{suit}" for rank in RANKS for suit in SUITS]
    rng = random.Random(seed)
    rng.shuffle(deck)
    return deck


def derive_seed(base_seed: int, hand_id: int, label: str) -> int:
    raw = f"{base_seed}:{hand_id}:{label}".encode("utf-8")
    digest = hashlib.sha256(raw).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False)
