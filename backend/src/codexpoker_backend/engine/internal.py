from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pokerkit import State

from codexpoker_backend.engine.models import (
    ActionHistoryRow,
    EventEnvelope,
    HandHistory,
    SessionOutcome,
    TableConfig,
)


@dataclass
class HandRuntime:
    hand_id: int
    dealer_button_seat: int
    sb_seat: int
    bb_seat: int
    active_seats: list[int]
    player_index_to_seat: dict[int, int]
    seat_to_player_index: dict[int, int]
    pokerkit_state: State
    deck: list[str]
    deck_index: int
    deal_seed: int
    bot_decision_seed: int
    bot_delay_seed: int
    action_seq: int = 0
    next_step_index: int = 1
    action_log_internal: list[ActionHistoryRow] = field(default_factory=list)
    event_log: list[EventEnvelope] = field(default_factory=list)
    event_cursor: int = 0
    committed_this_street_by_seat: dict[int, int] = field(default_factory=dict)
    total_committed_by_seat: dict[int, int] = field(default_factory=dict)
    hand_start_stacks_by_seat: dict[int, int] = field(default_factory=dict)
    idempotency_cache: dict[str, Any] = field(default_factory=dict)
    showdown_revealed_seats: set[int] = field(default_factory=set)
    showdown_rows_by_seat: dict[int, dict[str, Any]] = field(default_factory=dict)
    pot_awards: list[dict[str, Any]] = field(default_factory=list)
    folded_seats: set[int] = field(default_factory=set)
    all_in_seats: set[int] = field(default_factory=set)
    ended: bool = False
    hand_end_reason: str = ""
    event_seq: int = 0

    def next_card(self) -> str:
        card = self.deck[self.deck_index]
        self.deck_index += 1
        return card


@dataclass
class TableRuntime:
    table_id: str
    config: TableConfig
    seats: dict[int, dict[str, Any]]
    dealer_button_seat: int
    next_hand_id: int
    table_seed: int
    event_seq: int = 0
    outcome: SessionOutcome = SessionOutcome.RUNNING
    current_hand: HandRuntime | None = None
    completed_hands: dict[int, HandHistory] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()
