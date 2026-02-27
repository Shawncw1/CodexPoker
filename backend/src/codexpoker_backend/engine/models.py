from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


ENGINE_VERSION = "0.1.0"
RULESET_VERSION = "nlhe-cash-v1"


class PlayerType(str, Enum):
    HUMAN = "human"
    BOT = "bot"


class SessionOutcome(str, Enum):
    RUNNING = "running"
    HUMAN_WON = "human_won"
    HUMAN_LOST = "human_lost"


class Street(str, Enum):
    PREFLOP = "preflop"
    FLOP = "flop"
    TURN = "turn"
    RIVER = "river"
    SHOWDOWN = "showdown"
    HAND_ENDED = "hand_ended"


class ClientActionType(str, Enum):
    FOLD = "fold"
    CHECK = "check"
    CALL = "call"
    BET = "bet"
    RAISE = "raise"
    ALL_IN = "all_in"


class EventType(str, Enum):
    HAND_START = "HAND_START"
    POST_BLIND = "POST_BLIND"
    DEAL_CARD = "DEAL_CARD"
    ACTION = "ACTION"
    STREET_END_COLLECT = "STREET_END_COLLECT"
    BOARD_REVEAL = "BOARD_REVEAL"
    SHOWDOWN_REVEAL = "SHOWDOWN_REVEAL"
    POT_AWARD = "POT_AWARD"
    STACK_UPDATE = "STACK_UPDATE"
    HAND_END = "HAND_END"
    SESSION_END = "SESSION_END"


class TableConfig(BaseModel):
    num_seats: int = 6
    small_blind: int = 50
    big_blind: int = 100
    starting_stack: int = 10_000
    ante: int = 0
    no_rake: bool = True
    seed: int | None = None

    model_config = ConfigDict(extra="forbid")


class SeatState(BaseModel):
    seat_id: int
    player_type: PlayerType
    display_name: str
    stack: int
    has_folded: bool = False
    is_all_in: bool = False
    is_busted: bool = False
    role_badge: str | None = None
    is_dealer_button: bool = False
    cards: list[str | None] = Field(default_factory=lambda: [None, None])

    model_config = ConfigDict(extra="forbid")


class PotView(BaseModel):
    pot_id: int
    amount: int
    eligible_seats: list[int]
    label: str

    model_config = ConfigDict(extra="forbid")


class ActionLogEntry(BaseModel):
    event_seq: int
    seat_id: int
    action: ClientActionType
    amount_to: int | None = None
    street: Street

    model_config = ConfigDict(extra="forbid")


class AllowedActions(BaseModel):
    can_fold: bool = False
    can_check: bool = False
    can_call: bool = False
    can_bet: bool = False
    can_raise: bool = False
    can_all_in: bool = False
    call_amount: int = 0
    min_bet_to: int | None = None
    min_raise_to: int | None = None
    max_raise_to: int | None = None
    pot_size: int = 0
    effective_stack: int = 0

    model_config = ConfigDict(extra="forbid")


class ShowdownRow(BaseModel):
    seat_id: int
    player_name: str
    hole_cards: list[str]
    best_hand_name: str
    hand_rank_value: int
    amount_won: int

    model_config = ConfigDict(extra="forbid")


class ShowdownPayload(BaseModel):
    winners: list[ShowdownRow]
    losers: list[ShowdownRow]

    model_config = ConfigDict(extra="forbid")


class ViewState(BaseModel):
    table_id: str
    hand_id: int | None
    session_outcome: SessionOutcome
    seats: list[SeatState]
    board_cards: list[str]
    pots: list[PotView]
    chips_in_front: dict[int, int]
    action_on_seat: int | None
    turn_index: int | None
    action_clock_ms: int | None = None
    action_log: list[ActionLogEntry]
    server_action_seq: int
    allowed_actions: AllowedActions
    showdown_payload: ShowdownPayload | None = None
    state_hash: str
    invariant_hash: str
    speed_label: str = "1x"

    model_config = ConfigDict(extra="forbid")


class EngineError(BaseModel):
    code: str
    message: str

    model_config = ConfigDict(extra="forbid")


class EventEnvelope(BaseModel):
    table_id: str
    hand_id: int
    event_seq: int
    ts: str
    event_type: EventType
    payload: dict[str, Any]

    model_config = ConfigDict(extra="forbid")


class SubmitActionRequest(BaseModel):
    action: ClientActionType
    amount_to: int | None = None
    action_seq: int
    idempotency_key: str

    model_config = ConfigDict(extra="forbid")


class SubmitActionResponse(BaseModel):
    accepted: bool
    error: EngineError | None = None
    view_state: ViewState
    event_queue_delta: list[EventEnvelope]
    server_action_seq: int

    model_config = ConfigDict(extra="forbid")


class StartHandResponse(BaseModel):
    view_state: ViewState
    event_queue: list[EventEnvelope]

    model_config = ConfigDict(extra="forbid")


class ActionHistoryRow(BaseModel):
    step_index: int
    action_seq: int
    seat_id: int
    action: ClientActionType
    amount_to: int | None = None
    street: Street

    model_config = ConfigDict(extra="forbid")


class PotAwardHistoryRow(BaseModel):
    pot_id: int
    amount: int
    eligible_seats: list[int]
    winners: list[dict[str, int]]
    odd_chip_award: dict[str, int] | None = None

    model_config = ConfigDict(extra="forbid")


class HandHistory(BaseModel):
    hand_id: int
    table_id: str
    config: TableConfig
    initial_stacks_by_seat: dict[int, int]
    final_stacks_by_seat: dict[int, int]
    active_seats: list[int]
    player_index_to_seat: dict[int, int]
    dealer_button_seat: int
    sb_seat: int
    bb_seat: int
    deal_seed: int
    bot_decision_seed: int
    bot_delay_seed: int
    engine_version: str
    ruleset_version: str
    hole_cards_by_seat: dict[int, list[str | None]]
    board_cards: list[str]
    actions: list[ActionHistoryRow]
    pot_breakdown: list[PotAwardHistoryRow]
    showdown: ShowdownPayload | None
    hand_end_reason: str
    event_count: int
    events: list[EventEnvelope]

    model_config = ConfigDict(extra="forbid")


class ReplayResult(BaseModel):
    terminal_state: dict[str, Any]
    invariant_checks: dict[str, bool]

    model_config = ConfigDict(extra="forbid")
