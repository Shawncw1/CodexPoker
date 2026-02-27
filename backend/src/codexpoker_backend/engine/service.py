from __future__ import annotations

import asyncio
import json
import random
from collections import defaultdict
from typing import Any
from uuid import uuid4

from pokerkit import Mode, NoLimitTexasHoldem
from pokerkit.state import State

from codexpoker_backend.bots.policy import BotPolicy
from codexpoker_backend.engine.internal import HandRuntime, TableRuntime
from codexpoker_backend.engine.models import (
    ActionHistoryRow,
    ActionLogEntry,
    AllowedActions,
    ClientActionType,
    EngineError,
    EventEnvelope,
    EventType,
    HandHistory,
    PotAwardHistoryRow,
    PotView,
    ReplayResult,
    SeatState,
    SessionOutcome,
    ShowdownPayload,
    ShowdownRow,
    StartHandResponse,
    Street,
    SubmitActionResponse,
    TableConfig,
    ViewState,
    ENGINE_VERSION,
    RULESET_VERSION,
)
from codexpoker_backend.repo.base import TableRepository
from codexpoker_backend.utils.cards import build_shuffled_deck, derive_seed
from codexpoker_backend.utils.hashing import stable_hash


class EngineRejectedAction(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class PokerEngineService:
    def __init__(
        self,
        repository: TableRepository,
        bot_policy: BotPolicy | None = None,
    ) -> None:
        self._repo = repository
        self._bot_policy = bot_policy or BotPolicy()
        self._subscriptions: dict[str, set[asyncio.Queue[EventEnvelope]]] = defaultdict(set)

    async def create_table(self, config: TableConfig) -> str:
        table_id = f"tbl_{uuid4().hex[:12]}"
        table_seed = config.seed if config.seed is not None else random.randrange(1, 2**63)

        seats: dict[int, dict[str, Any]] = {}
        for seat_id in range(config.num_seats):
            seats[seat_id] = {
                "seat_id": seat_id,
                "player_type": "human" if seat_id == 0 else "bot",
                "display_name": "You" if seat_id == 0 else f"Bot {seat_id}",
                "stack": config.starting_stack,
            }

        table = TableRuntime(
            table_id=table_id,
            config=config,
            seats=seats,
            dealer_button_seat=config.num_seats - 1,
            next_hand_id=1,
            table_seed=table_seed,
        )
        self._repo.create(table)
        return table_id

    async def start_new_hand(self, table_id: str) -> StartHandResponse:
        table = self._repo.get(table_id)
        async with table.lock:
            if table.current_hand is not None:
                raise EngineRejectedAction("HAND_ALREADY_RUNNING", "A hand is already in progress.")

            self._refresh_outcome(table)
            if table.outcome is not SessionOutcome.RUNNING:
                view = self._build_view_state(table, "human")
                return StartHandResponse(view_state=view, event_queue=[])

            hand = self._start_hand_locked(table)
            self._advance_locked(table, hand)
            view = self._build_view_state(table, "human")
            return StartHandResponse(view_state=view, event_queue=list(hand.event_log))

    async def get_view_state(self, table_id: str, viewer_id: str = "human") -> ViewState:
        table = self._repo.get(table_id)
        async with table.lock:
            return self._build_view_state(table, viewer_id)

    async def get_allowed_actions(
        self,
        table_id: str,
        viewer_id: str = "human",
    ) -> AllowedActions:
        table = self._repo.get(table_id)
        async with table.lock:
            return self._allowed_actions_for_viewer(table, viewer_id)

    async def submit_action(
        self,
        table_id: str,
        viewer_id: str,
        action: ClientActionType,
        action_seq: int,
        idempotency_key: str,
        amount_to: int | None = None,
    ) -> SubmitActionResponse:
        table = self._repo.get(table_id)

        async with table.lock:
            hand = table.current_hand
            if hand is None:
                raise EngineRejectedAction("NO_ACTIVE_HAND", "No active hand to act in.")

            if idempotency_key in hand.idempotency_cache:
                return hand.idempotency_cache[idempotency_key]

            if viewer_id != "human":
                raise EngineRejectedAction("UNAUTHORIZED_VIEWER", "Only the human viewer can submit actions.")

            if action_seq != hand.action_seq + 1:
                raise EngineRejectedAction(
                    "BAD_ACTION_SEQ",
                    f"Expected action_seq {hand.action_seq + 1}, got {action_seq}.",
                )

            actor_seat = self._actor_seat(hand)
            if actor_seat != 0:
                raise EngineRejectedAction("NOT_YOUR_TURN", "It is not the human seat's turn.")

            start_index = len(hand.event_log)
            try:
                self._apply_player_action(table, hand, actor_seat, action, amount_to, think_delay_ms=None)
            except EngineRejectedAction as exc:
                view = self._build_view_state(table, viewer_id)
                return SubmitActionResponse(
                    accepted=False,
                    error=EngineError(code=exc.code, message=exc.message),
                    view_state=view,
                    event_queue_delta=[],
                    server_action_seq=hand.action_seq,
                )

            self._advance_locked(table, hand)
            view = self._build_view_state(table, viewer_id)
            response = SubmitActionResponse(
                accepted=True,
                view_state=view,
                event_queue_delta=hand.event_log[start_index:],
                server_action_seq=hand.action_seq,
            )
            hand.idempotency_cache[idempotency_key] = response
            return response

    async def advance(self, table_id: str) -> tuple[ViewState, list[EventEnvelope]]:
        table = self._repo.get(table_id)
        async with table.lock:
            hand = table.current_hand
            if hand is None:
                return self._build_view_state(table, "human"), []
            start = len(hand.event_log)
            self._advance_locked(table, hand)
            return self._build_view_state(table, "human"), hand.event_log[start:]

    async def run_bots_until_human_turn(self, table_id: str) -> tuple[ViewState, list[EventEnvelope]]:
        table = self._repo.get(table_id)
        async with table.lock:
            hand = table.current_hand
            if hand is None:
                return self._build_view_state(table, "human"), []

            event_start = len(hand.event_log)
            guard = 0
            while True:
                guard += 1
                if guard > 2_000:
                    raise RuntimeError("bot runner exceeded guard limit")

                actor_seat = self._actor_seat(hand)
                if actor_seat is None:
                    self._advance_locked(table, hand)
                    if hand.ended:
                        table.current_hand = None
                        self._refresh_outcome(table)
                        break
                    continue

                if actor_seat == 0:
                    break

                allowed = self._allowed_actions_for_seat(table, hand, actor_seat)
                stack = table.seats[actor_seat]["stack"]
                rng = random.Random(
                    derive_seed(hand.bot_decision_seed, hand.action_seq + actor_seat, "decision"),
                )
                decision = self._bot_policy.choose_action(allowed=allowed, stack=stack, rng=rng)
                self._apply_player_action(
                    table,
                    hand,
                    actor_seat,
                    decision.action,
                    decision.amount_to,
                    think_delay_ms=decision.think_delay_ms,
                )
                self._advance_locked(table, hand)
                if hand.ended:
                    table.current_hand = None
                    self._refresh_outcome(table)
                    break

            return self._build_view_state(table, "human"), hand.event_log[event_start:]

    async def export_hand_history(
        self,
        table_id: str,
        hand_id: int,
        mode: str = "viewer",
    ) -> dict[str, Any]:
        table = self._repo.get(table_id)
        async with table.lock:
            if hand_id not in table.completed_hands:
                raise EngineRejectedAction("HAND_NOT_FOUND", f"Hand {hand_id} does not exist.")
            hand = table.completed_hands[hand_id]
            payload = hand.model_dump(mode="json")
            if mode == "viewer":
                masked = payload["hole_cards_by_seat"]
                for seat_key, cards in masked.items():
                    if int(seat_key) != 0 and cards is not None:
                        if payload.get("showdown") is None:
                            masked[seat_key] = [None, None]
                payload["hole_cards_by_seat"] = masked
            return payload

    async def replay_hand_history(self, hand_history_json: dict[str, Any]) -> ReplayResult:
        hand_history = HandHistory.model_validate(hand_history_json)

        simulation = self._replay_from_history(hand_history)
        terminal = {
            "hand_id": hand_history.hand_id,
            "final_stacks_by_seat": simulation["final_stacks_by_seat"],
            "board_cards": simulation["board_cards"],
            "showdown": simulation["showdown"],
            "hand_end_reason": simulation["hand_end_reason"],
        }
        checks = {
            "chip_conservation": simulation["chip_conservation"],
            "hand_terminated": simulation["hand_terminated"],
            "action_replay_match": simulation["action_replay_match"],
            "event_seq_monotonic": simulation["event_seq_monotonic"],
        }
        return ReplayResult(terminal_state=terminal, invariant_checks=checks)

    async def restart_session(self, table_id: str) -> StartHandResponse:
        table = self._repo.get(table_id)
        async with table.lock:
            for seat in table.seats.values():
                seat["stack"] = table.config.starting_stack
            table.outcome = SessionOutcome.RUNNING
            table.current_hand = None
            table.completed_hands.clear()
            table.dealer_button_seat = table.config.num_seats - 1
            table.next_hand_id = 1
            hand = self._start_hand_locked(table)
            self._advance_locked(table, hand)
            return StartHandResponse(view_state=self._build_view_state(table, "human"), event_queue=list(hand.event_log))

    async def get_server_action_seq(self, table_id: str) -> int:
        table = self._repo.get(table_id)
        async with table.lock:
            if table.current_hand is None:
                return 0
            return table.current_hand.action_seq

    async def subscribe(self, table_id: str) -> asyncio.Queue[EventEnvelope]:
        table = self._repo.get(table_id)
        queue: asyncio.Queue[EventEnvelope] = asyncio.Queue(maxsize=256)
        async with table.lock:
            self._subscriptions[table_id].add(queue)
        return queue

    async def unsubscribe(self, table_id: str, queue: asyncio.Queue[EventEnvelope]) -> None:
        table = self._repo.get(table_id)
        async with table.lock:
            self._subscriptions[table_id].discard(queue)

    def _active_seats(self, table: TableRuntime) -> list[int]:
        return [seat_id for seat_id, seat in table.seats.items() if seat["stack"] > 0]

    def _next_active_after(self, seat_id: int, active: list[int], total_seats: int) -> int:
        active_set = set(active)
        cursor = seat_id
        for _ in range(total_seats):
            cursor = (cursor + 1) % total_seats
            if cursor in active_set:
                return cursor
        raise RuntimeError("failed to find active seat")

    def _start_hand_locked(self, table: TableRuntime) -> HandRuntime:
        active_seats = self._active_seats(table)
        if len(active_seats) < 2:
            self._refresh_outcome(table)
            raise EngineRejectedAction("NOT_ENOUGH_PLAYERS", "Not enough players with chips.")

        if table.dealer_button_seat not in active_seats:
            dealer = self._next_active_after(
                table.dealer_button_seat,
                active_seats,
                table.config.num_seats,
            )
        else:
            dealer = table.dealer_button_seat

        if len(active_seats) == 2:
            sb_seat = dealer
            bb_seat = [seat for seat in active_seats if seat != dealer][0]
            player_index_to_seat = {0: bb_seat, 1: sb_seat}
        else:
            sb_seat = self._next_active_after(dealer, active_seats, table.config.num_seats)
            bb_seat = self._next_active_after(sb_seat, active_seats, table.config.num_seats)
            ordered = [sb_seat, bb_seat]
            cursor = bb_seat
            while len(ordered) < len(active_seats):
                cursor = self._next_active_after(cursor, active_seats, table.config.num_seats)
                if cursor not in ordered:
                    ordered.append(cursor)
            player_index_to_seat = {index: seat for index, seat in enumerate(ordered)}

        seat_to_player_index = {seat: index for index, seat in player_index_to_seat.items()}
        starting_stacks = [table.seats[player_index_to_seat[i]]["stack"] for i in range(len(active_seats))]

        hand_id = table.next_hand_id
        table.next_hand_id += 1
        table.dealer_button_seat = self._next_active_after(dealer, active_seats, table.config.num_seats)

        deal_seed = derive_seed(table.table_seed, hand_id, "deal")
        bot_seed = derive_seed(table.table_seed, hand_id, "bot_decision")
        bot_delay_seed = derive_seed(table.table_seed, hand_id, "bot_delay")

        state = NoLimitTexasHoldem.create_state(
            (),
            True,
            table.config.ante,
            (table.config.small_blind, table.config.big_blind),
            table.config.big_blind,
            tuple(starting_stacks),
            len(active_seats),
            mode=Mode.CASH_GAME,
            rake=lambda amount, _: (0, amount),
        )

        hand = HandRuntime(
            hand_id=hand_id,
            dealer_button_seat=dealer,
            sb_seat=sb_seat,
            bb_seat=bb_seat,
            active_seats=active_seats,
            player_index_to_seat=player_index_to_seat,
            seat_to_player_index=seat_to_player_index,
            pokerkit_state=state,
            deck=build_shuffled_deck(deal_seed),
            deck_index=0,
            deal_seed=deal_seed,
            bot_decision_seed=bot_seed,
            bot_delay_seed=bot_delay_seed,
            committed_this_street_by_seat={seat: 0 for seat in range(table.config.num_seats)},
            total_committed_by_seat={seat: 0 for seat in range(table.config.num_seats)},
            hand_start_stacks_by_seat={seat: table.seats[seat]["stack"] for seat in range(table.config.num_seats)},
        )
        hand.showdown_rows_by_seat = {
            seat: {"hole_cards": [], "amount_won": 0}
            for seat in hand.active_seats
        }
        table.current_hand = hand

        self._emit_event(
            table,
            hand,
            EventType.HAND_START,
            {
                "dealer_button_seat": dealer,
                "sb_seat": sb_seat,
                "bb_seat": bb_seat,
                "starting_stacks": hand.hand_start_stacks_by_seat,
            },
        )
        self._post_blinds_locked(table, hand)
        self._deal_hole_cards_locked(table, hand)
        self._sync_table_stacks_from_state(table, hand.pokerkit_state, hand)
        return hand

    def _post_blinds_locked(self, table: TableRuntime, hand: HandRuntime) -> None:
        state = hand.pokerkit_state
        while state.can_post_blind_or_straddle():
            self._post_single_blind_locked(table, hand)

    def _post_single_blind_locked(self, table: TableRuntime, hand: HandRuntime) -> None:
        state = hand.pokerkit_state
        op = state.post_blind_or_straddle()
        seat = hand.player_index_to_seat[op.player_index]
        hand.committed_this_street_by_seat[seat] += op.amount
        hand.total_committed_by_seat[seat] += op.amount
        self._emit_event(
            table,
            hand,
            EventType.POST_BLIND,
            {"seat": seat, "amount": op.amount},
        )

    def _deal_hole_cards_locked(self, table: TableRuntime, hand: HandRuntime) -> None:
        state = hand.pokerkit_state
        while state.can_deal_hole():
            self._deal_single_hole_card_locked(table, hand)

    def _deal_single_hole_card_locked(self, table: TableRuntime, hand: HandRuntime) -> None:
        state = hand.pokerkit_state
        card = hand.next_card()
        op = state.deal_hole(card)
        seat = hand.player_index_to_seat[op.player_index]
        seat_row = hand.showdown_rows_by_seat.setdefault(seat, {"hole_cards": [], "amount_won": 0})
        hole_cards = seat_row.setdefault("hole_cards", [])
        hole_cards.append(card)
        card_index = len(hole_cards) - 1
        self._emit_event(
            table,
            hand,
            EventType.DEAL_CARD,
            {
                "to_seat": seat,
                "card_index": card_index,
                "card": card if seat == 0 else None,
            },
        )

    def _advance_locked(self, table: TableRuntime, hand: HandRuntime) -> None:
        state = hand.pokerkit_state

        iteration_guard = 0
        while True:
            iteration_guard += 1
            if iteration_guard > 500:
                raise RuntimeError("advance loop exceeded guard limit")

            if not state.status:
                break

            if state.can_post_ante():
                op = state.post_ante()
                seat = hand.player_index_to_seat.get(op.player_index)
                if seat is not None:
                    hand.committed_this_street_by_seat[seat] += op.amount
                    hand.total_committed_by_seat[seat] += op.amount
                continue

            if state.can_post_blind_or_straddle():
                self._post_single_blind_locked(table, hand)
                continue

            if state.can_deal_hole():
                self._deal_single_hole_card_locked(table, hand)
                continue

            if state.actor_index is not None:
                self._sync_table_stacks_from_state(table, state, hand)
                return

            if state.can_collect_bets():
                collected = tuple(state.bets)
                state.collect_bets()
                for seat in hand.committed_this_street_by_seat:
                    hand.committed_this_street_by_seat[seat] = 0
                self._emit_event(
                    table,
                    hand,
                    EventType.STREET_END_COLLECT,
                    {"collected": list(collected)},
                )
                continue

            if state.can_select_runout_count():
                state.select_runout_count(1)
                continue

            if state.can_burn_card():
                state.burn_card(hand.next_card())
                continue

            if state.can_deal_board():
                count = state.board_dealing_count
                cards = [hand.next_card() for _ in range(count)]
                state.deal_board("".join(cards))
                self._emit_event(
                    table,
                    hand,
                    EventType.BOARD_REVEAL,
                    {
                        "street": self._street_for_board_count(len(list(state.get_board_cards(0)))),
                        "cards": cards,
                    },
                )
                continue

            if state.can_show_or_muck_hole_cards():
                op = state.show_or_muck_hole_cards(True)
                seat = hand.player_index_to_seat[op.player_index]
                hand.showdown_revealed_seats.add(seat)
                cards = [str(card) for card in op.hole_cards]
                hand.showdown_rows_by_seat[seat]["hole_cards"] = cards
                self._emit_event(
                    table,
                    hand,
                    EventType.SHOWDOWN_REVEAL,
                    {"seats_revealed": [seat], "hole_cards": {seat: cards}},
                )
                continue

            if state.can_kill_hand():
                state.kill_hand()
                continue

            if state.can_push_chips():
                op = state.push_chips()
                pot = state._pots[op.pot_index] if state._pots else None  # noqa: SLF001
                winners = []
                total_awarded = 0
                winner_seats = []
                for player_index, amount in enumerate(op.amounts):
                    if amount <= 0:
                        continue
                    seat = hand.player_index_to_seat[player_index]
                    winners.append({"seat": seat, "amount": amount})
                    winner_seats.append(seat)
                    total_awarded += amount
                    hand.showdown_rows_by_seat.setdefault(
                        seat,
                        {"hole_cards": [None, None], "amount_won": 0},
                    )["amount_won"] += amount

                odd_chip_award = None
                if winner_seats and len(winner_seats) > 1:
                    even_share = total_awarded // len(winner_seats)
                    remainder = total_awarded - even_share * len(winner_seats)
                    if remainder > 0:
                        odd_chip_award = {"seat": winner_seats[0], "amount": remainder}

                pot_payload = {
                    "pot_id": op.pot_index,
                    "amount": total_awarded,
                    "winners": winners,
                    "eligible_seats": [hand.player_index_to_seat[i] for i in (pot.player_indices if pot else ())],
                    "odd_chip_award": odd_chip_award,
                }
                hand.pot_awards.append(pot_payload)
                self._emit_event(table, hand, EventType.POT_AWARD, pot_payload)
                continue

            if state.can_pull_chips():
                op = state.pull_chips()
                seat = hand.player_index_to_seat[op.player_index]
                self._sync_table_stacks_from_state(table, state, hand)
                self._emit_event(
                    table,
                    hand,
                    EventType.STACK_UPDATE,
                    {"seat": seat, "new_stack": table.seats[seat]["stack"]},
                )
                continue

            if state.can_no_operate():
                state.no_operate()
                continue

            break

        self._sync_table_stacks_from_state(table, state, hand)
        if not state.status and not hand.ended:
            self._complete_hand_locked(table, hand)

    def _apply_player_action(
        self,
        table: TableRuntime,
        hand: HandRuntime,
        seat: int,
        action: ClientActionType,
        amount_to: int | None,
        think_delay_ms: int | None,
    ) -> None:
        state = hand.pokerkit_state
        actor_seat = self._actor_seat(hand)
        if actor_seat != seat:
            raise EngineRejectedAction("NOT_TURN", "Action does not match current actor.")

        action_to_log = action
        amount_logged = amount_to

        if action is ClientActionType.FOLD:
            if not state.can_fold():
                raise EngineRejectedAction("ACTION_NOT_ALLOWED", "Fold is not allowed.")
            state.fold()
            hand.folded_seats.add(seat)
        elif action in (ClientActionType.CHECK, ClientActionType.CALL):
            if not state.can_check_or_call():
                raise EngineRejectedAction("ACTION_NOT_ALLOWED", "Check/call is not allowed.")
            call_amount = state.checking_or_calling_amount or 0
            state.check_or_call()
            amount_logged = call_amount if call_amount > 0 else None
            if call_amount > 0 and table.seats[seat]["stack"] == call_amount:
                action_to_log = ClientActionType.ALL_IN
                hand.all_in_seats.add(seat)
            elif call_amount == 0:
                action_to_log = ClientActionType.CHECK
            else:
                action_to_log = ClientActionType.CALL
            hand.committed_this_street_by_seat[seat] += call_amount
            hand.total_committed_by_seat[seat] += call_amount
        elif action in (ClientActionType.BET, ClientActionType.RAISE):
            if amount_to is None:
                raise EngineRejectedAction("MISSING_AMOUNT", "bet/raise requires amount_to.")
            if not state.can_complete_bet_or_raise_to():
                raise EngineRejectedAction("ACTION_NOT_ALLOWED", "Bet/raise is not allowed.")
            min_to = state.min_completion_betting_or_raising_to_amount
            max_to = state.max_completion_betting_or_raising_to_amount
            if min_to is None or max_to is None:
                raise EngineRejectedAction("ACTION_NOT_ALLOWED", "Bet/raise not available.")
            if amount_to < min_to or amount_to > max_to:
                raise EngineRejectedAction(
                    "INVALID_SIZING",
                    f"Amount {amount_to} outside [{min_to}, {max_to}].",
                )
            before = state.bets[state.actor_index]
            state.complete_bet_or_raise_to(amount_to)
            delta = amount_to - before
            hand.committed_this_street_by_seat[seat] += delta
            hand.total_committed_by_seat[seat] += delta
            if amount_to == max_to:
                action_to_log = ClientActionType.ALL_IN
                hand.all_in_seats.add(seat)
        elif action is ClientActionType.ALL_IN:
            max_to = state.max_completion_betting_or_raising_to_amount
            if state.can_complete_bet_or_raise_to() and max_to is not None:
                before = state.bets[state.actor_index]
                state.complete_bet_or_raise_to(max_to)
                delta = max_to - before
                hand.committed_this_street_by_seat[seat] += delta
                hand.total_committed_by_seat[seat] += delta
                amount_logged = max_to
            elif state.can_check_or_call():
                call_amount = state.checking_or_calling_amount or 0
                state.check_or_call()
                hand.committed_this_street_by_seat[seat] += call_amount
                hand.total_committed_by_seat[seat] += call_amount
                amount_logged = call_amount if call_amount > 0 else None
            else:
                raise EngineRejectedAction("ACTION_NOT_ALLOWED", "All-in is not currently allowed.")
            hand.all_in_seats.add(seat)
            action_to_log = ClientActionType.ALL_IN
        else:
            raise EngineRejectedAction("UNKNOWN_ACTION", f"Unsupported action {action}.")

        hand.action_seq += 1
        entry = ActionHistoryRow(
            step_index=hand.next_step_index,
            action_seq=hand.action_seq,
            seat_id=seat,
            action=action_to_log,
            amount_to=amount_logged,
            street=self._street_from_state(hand.pokerkit_state),
        )
        hand.next_step_index += 1
        hand.action_log_internal.append(entry)
        self._sync_table_stacks_from_state(table, state, hand)
        event_payload: dict[str, Any] = {"seat": seat, "action_type": action_to_log, "amount_to": amount_logged}
        if think_delay_ms is not None:
            event_payload["bot_think_delay_ms"] = think_delay_ms
        self._emit_event(table, hand, EventType.ACTION, event_payload)

    def _complete_hand_locked(self, table: TableRuntime, hand: HandRuntime) -> None:
        state = hand.pokerkit_state
        hand.ended = True
        hand.hand_end_reason = "showdown" if hand.showdown_revealed_seats else "all_folded"
        showdown = self._build_showdown_payload(hand, state)
        self._sync_table_stacks_from_state(table, state, hand)
        self._emit_event(
            table,
            hand,
            EventType.HAND_END,
            {
                "reason": hand.hand_end_reason,
                "final_stacks_by_seat": {seat: data["stack"] for seat, data in table.seats.items()},
            },
        )

        history = HandHistory(
            hand_id=hand.hand_id,
            table_id=table.table_id,
            config=table.config,
            initial_stacks_by_seat=hand.hand_start_stacks_by_seat,
            final_stacks_by_seat={seat: data["stack"] for seat, data in table.seats.items()},
            active_seats=hand.active_seats,
            player_index_to_seat=hand.player_index_to_seat,
            dealer_button_seat=hand.dealer_button_seat,
            sb_seat=hand.sb_seat,
            bb_seat=hand.bb_seat,
            deal_seed=hand.deal_seed,
            bot_decision_seed=hand.bot_decision_seed,
            bot_delay_seed=hand.bot_delay_seed,
            engine_version=ENGINE_VERSION,
            ruleset_version=RULESET_VERSION,
            hole_cards_by_seat={
                seat: self._normalize_hole_cards(
                    hand.showdown_rows_by_seat.get(seat, {}).get("hole_cards", []),
                )
                for seat in range(table.config.num_seats)
            },
            board_cards=[str(card) for card in state.get_board_cards(0)],
            actions=hand.action_log_internal,
            pot_breakdown=[
                PotAwardHistoryRow(
                    pot_id=pot_award["pot_id"],
                    amount=pot_award["amount"],
                    eligible_seats=pot_award["eligible_seats"],
                    winners=pot_award["winners"],
                    odd_chip_award=pot_award["odd_chip_award"],
                )
                for pot_award in hand.pot_awards
            ],
            showdown=showdown,
            hand_end_reason=hand.hand_end_reason,
            event_count=len(hand.event_log),
            events=hand.event_log,
        )
        table.completed_hands[hand.hand_id] = history
        self._refresh_outcome(table)
        if table.outcome is not SessionOutcome.RUNNING:
            self._emit_event(
                table,
                hand,
                EventType.SESSION_END,
                {"outcome": table.outcome},
            )

    def _refresh_outcome(self, table: TableRuntime) -> None:
        human_stack = table.seats[0]["stack"]
        bot_stacks = [seat["stack"] for seat_id, seat in table.seats.items() if seat_id != 0]
        if human_stack <= 0:
            table.outcome = SessionOutcome.HUMAN_LOST
        elif all(stack <= 0 for stack in bot_stacks):
            table.outcome = SessionOutcome.HUMAN_WON
        else:
            table.outcome = SessionOutcome.RUNNING

    def _allowed_actions_for_viewer(self, table: TableRuntime, viewer_id: str) -> AllowedActions:
        if viewer_id != "human":
            return AllowedActions()
        hand = table.current_hand
        if hand is None:
            return AllowedActions()
        return self._allowed_actions_for_seat(table, hand, 0)

    def _allowed_actions_for_seat(self, table: TableRuntime, hand: HandRuntime, seat: int) -> AllowedActions:
        state = hand.pokerkit_state
        actor_seat = self._actor_seat(hand)
        if actor_seat != seat:
            return AllowedActions(pot_size=state.total_pot_amount, effective_stack=table.seats[seat]["stack"])

        call_amount = state.checking_or_calling_amount or 0
        can_complete = state.can_complete_bet_or_raise_to()
        min_to = state.min_completion_betting_or_raising_to_amount if can_complete else None
        max_to = state.max_completion_betting_or_raising_to_amount if can_complete else None
        has_live_bet = max(state.bets) > 0

        return AllowedActions(
            can_fold=state.can_fold(),
            can_check=state.can_check_or_call() and call_amount == 0,
            can_call=state.can_check_or_call() and call_amount > 0,
            can_bet=can_complete and not has_live_bet,
            can_raise=can_complete and has_live_bet,
            can_all_in=(state.can_check_or_call() or can_complete) and table.seats[seat]["stack"] > 0,
            call_amount=call_amount,
            min_bet_to=min_to if not has_live_bet else None,
            min_raise_to=min_to if has_live_bet else None,
            max_raise_to=max_to,
            pot_size=state.total_pot_amount,
            effective_stack=table.seats[seat]["stack"],
        )

    def _build_view_state(self, table: TableRuntime, viewer_id: str) -> ViewState:
        hand = table.current_hand
        seats = []
        board_cards: list[str] = []
        pots: list[PotView] = []
        chips_in_front = {seat: 0 for seat in table.seats}
        action_log: list[ActionLogEntry] = []
        showdown_payload = None
        hand_id = None
        action_on_seat = None

        for seat_id, raw in sorted(table.seats.items()):
            seat = SeatState(
                seat_id=seat_id,
                player_type=raw["player_type"],
                display_name=raw["display_name"],
                stack=raw["stack"],
                is_busted=raw["stack"] <= 0,
                cards=[None, None],
            )
            seats.append(seat)

        if hand is not None:
            hand_id = hand.hand_id
            state = hand.pokerkit_state
            board_cards = [str(card) for card in state.get_board_cards(0)]
            for player_index, seat_id in hand.player_index_to_seat.items():
                chips_in_front[seat_id] = state.bets[player_index]
                seats[seat_id].stack = table.seats[seat_id]["stack"]
                seats[seat_id].has_folded = seat_id in hand.folded_seats
                seats[seat_id].is_all_in = seat_id in hand.all_in_seats
                if seat_id == hand.sb_seat:
                    seats[seat_id].role_badge = "SB"
                elif seat_id == hand.bb_seat:
                    seats[seat_id].role_badge = "BB"
                seats[seat_id].is_dealer_button = seat_id == hand.dealer_button_seat

                cards = hand.showdown_rows_by_seat.get(seat_id, {}).get("hole_cards", [None, None])
                if seat_id == 0:
                    seats[seat_id].cards = self._normalize_hole_cards(cards)
                elif seat_id in hand.showdown_revealed_seats:
                    seats[seat_id].cards = self._normalize_hole_cards(cards)
                else:
                    seats[seat_id].cards = [None, None]

            for index, pot in enumerate(state.pots):
                seat_list = [hand.player_index_to_seat[player_index] for player_index in pot.player_indices]
                label = "POT" if index == 0 else f"SIDE POT {index}"
                pots.append(PotView(pot_id=index, amount=pot.amount, eligible_seats=seat_list, label=label))

            action_on_seat = self._actor_seat(hand)
            action_log = [
                ActionLogEntry(
                    event_seq=index + 1,
                    seat_id=row.seat_id,
                    action=row.action,
                    amount_to=row.amount_to,
                    street=row.street,
                )
                for index, row in enumerate(hand.action_log_internal[-12:])
            ]

            showdown_payload = self._build_showdown_payload(hand, state)

        allowed = self._allowed_actions_for_viewer(table, viewer_id)
        raw_state = {
            "table_id": table.table_id,
            "hand_id": hand_id,
            "outcome": table.outcome,
            "seats": [seat.model_dump(mode="json") for seat in seats],
            "board": board_cards,
            "pots": [pot.model_dump(mode="json") for pot in pots],
            "chips_in_front": chips_in_front,
            "actor": action_on_seat,
            "allowed": allowed.model_dump(mode="json"),
        }
        state_hash = stable_hash(raw_state)
        invariant_hash = stable_hash(self._invariant_payload(table, hand))

        return ViewState(
            table_id=table.table_id,
            hand_id=hand_id,
            session_outcome=table.outcome,
            seats=seats,
            board_cards=board_cards,
            pots=pots,
            chips_in_front=chips_in_front,
            action_on_seat=action_on_seat,
            turn_index=action_on_seat,
            action_log=action_log,
            server_action_seq=hand.action_seq if hand is not None else 0,
            allowed_actions=allowed,
            showdown_payload=showdown_payload,
            state_hash=state_hash,
            invariant_hash=invariant_hash,
        )

    def _build_showdown_payload(self, hand: HandRuntime, state: State) -> ShowdownPayload | None:
        if not hand.showdown_revealed_seats:
            return None

        rows = []
        for seat in sorted(hand.showdown_revealed_seats):
            player_index = hand.seat_to_player_index[seat]
            hand_obj = state.get_hand(player_index, 0, 0)
            if hand_obj is None:
                continue
            amount_won = hand.showdown_rows_by_seat.get(seat, {}).get("amount_won", 0)
            rows.append(
                ShowdownRow(
                    seat_id=seat,
                    player_name="You" if seat == 0 else f"Bot {seat}",
                    hole_cards=[
                        str(card)
                        for card in self._normalize_hole_cards(
                            hand.showdown_rows_by_seat.get(seat, {}).get("hole_cards", []),
                        )
                        if card is not None
                    ],
                    best_hand_name=hand_obj.entry.label.value,
                    hand_rank_value=hand_obj.entry.index,
                    amount_won=amount_won,
                ),
            )

        winners = sorted([row for row in rows if row.amount_won > 0], key=lambda item: item.amount_won, reverse=True)
        losers = sorted([row for row in rows if row.amount_won == 0], key=lambda item: item.hand_rank_value, reverse=True)
        return ShowdownPayload(winners=winners, losers=losers)

    def _actor_seat(self, hand: HandRuntime) -> int | None:
        actor = hand.pokerkit_state.actor_index
        if actor is None:
            return None
        return hand.player_index_to_seat[actor]

    def _sync_table_stacks_from_state(self, table: TableRuntime, state: State, hand: HandRuntime) -> None:
        for player_index, seat in hand.player_index_to_seat.items():
            table.seats[seat]["stack"] = state.stacks[player_index]

    def _street_from_state(self, state: State) -> Street:
        board_count = len(list(state.get_board_cards(0)))
        if board_count == 0:
            return Street.PREFLOP
        if board_count == 3:
            return Street.FLOP
        if board_count == 4:
            return Street.TURN
        if board_count == 5 and state.status:
            return Street.RIVER
        return Street.SHOWDOWN

    def _street_for_board_count(self, count: int) -> Street:
        if count == 3:
            return Street.FLOP
        if count == 4:
            return Street.TURN
        if count == 5:
            return Street.RIVER
        return Street.PREFLOP

    def _normalize_hole_cards(self, cards: list[str | None]) -> list[str | None]:
        normalized = list(cards[:2])
        while len(normalized) < 2:
            normalized.append(None)
        return normalized

    def _emit_event(
        self,
        table: TableRuntime,
        hand: HandRuntime,
        event_type: EventType,
        payload: dict[str, Any],
    ) -> None:
        table.event_seq += 1
        hand.event_seq = table.event_seq
        envelope = EventEnvelope(
            table_id=table.table_id,
            hand_id=hand.hand_id,
            event_seq=table.event_seq,
            ts=table.now_iso(),
            event_type=event_type,
            payload=payload,
        )
        hand.event_log.append(envelope)
        for queue in list(self._subscriptions.get(table.table_id, set())):
            try:
                queue.put_nowait(envelope)
            except asyncio.QueueFull:
                continue

    def _invariant_payload(self, table: TableRuntime, hand: HandRuntime | None) -> dict[str, Any]:
        total_chips = sum(seat["stack"] for seat in table.seats.values())
        in_front = 0
        status = True
        if hand is not None:
            state = hand.pokerkit_state
            in_front = sum(state.bets)
            total_chips += in_front + sum(pot.amount for pot in state.pots)
            status = state.status

        return {
            "chip_conservation": total_chips == table.config.starting_stack * table.config.num_seats,
            "no_negative_stacks": all(seat["stack"] >= 0 for seat in table.seats.values()),
            "hand_status_valid": status in (True, False),
            "in_front": in_front,
        }

    def _replay_from_history(self, hand_history: HandHistory) -> dict[str, Any]:
        config = hand_history.config
        active_seats = hand_history.active_seats
        total_chips_target = sum(hand_history.initial_stacks_by_seat.values())
        player_index_to_seat = {int(k): v for k, v in hand_history.player_index_to_seat.items()}
        seat_to_player_index = {seat: idx for idx, seat in player_index_to_seat.items()}
        starting_stacks = [hand_history.initial_stacks_by_seat[player_index_to_seat[i]] for i in range(len(active_seats))]

        state = NoLimitTexasHoldem.create_state(
            (),
            True,
            config.ante,
            (config.small_blind, config.big_blind),
            config.big_blind,
            tuple(starting_stacks),
            len(active_seats),
            mode=Mode.CASH_GAME,
            rake=lambda amount, _: (0, amount),
        )

        deck = build_shuffled_deck(hand_history.deal_seed)
        deck_idx = 0
        while state.can_post_blind_or_straddle():
            state.post_blind_or_straddle()
        while state.can_deal_hole():
            state.deal_hole(deck[deck_idx])
            deck_idx += 1

        for row in hand_history.actions:
            if state.actor_index is None:
                self._replay_advance(state, deck, deck_idx)
                deck_idx = self._deck_index_after_advance(state, deck, deck_idx)

            actor = state.actor_index
            if actor is None:
                continue
            seat = player_index_to_seat[actor]
            if seat != row.seat_id:
                raise AssertionError(f"replay seat mismatch: expected {row.seat_id}, got {seat}")

            if row.action is ClientActionType.FOLD:
                state.fold()
            elif row.action in (ClientActionType.CHECK, ClientActionType.CALL):
                state.check_or_call()
            elif row.action in (ClientActionType.BET, ClientActionType.RAISE):
                if row.amount_to is None:
                    raise AssertionError("missing amount_to during replay raise")
                state.complete_bet_or_raise_to(row.amount_to)
            elif row.action is ClientActionType.ALL_IN:
                if state.can_complete_bet_or_raise_to():
                    max_to = state.max_completion_betting_or_raising_to_amount
                    if max_to is None:
                        raise AssertionError("max_to missing for all-in")
                    state.complete_bet_or_raise_to(max_to)
                else:
                    state.check_or_call()

        iterations = 0
        while state.status:
            iterations += 1
            if iterations > 1000:
                raise AssertionError("replay loop exceeded guard limit")
            progressed = False
            if state.actor_index is not None:
                state.check_or_call()
                progressed = True
            elif state.can_collect_bets():
                state.collect_bets()
                progressed = True
            elif state.can_select_runout_count():
                state.select_runout_count(1)
                progressed = True
            elif state.can_burn_card():
                state.burn_card(deck[deck_idx])
                deck_idx += 1
                progressed = True
            elif state.can_deal_board():
                count = state.board_dealing_count
                state.deal_board("".join(deck[deck_idx : deck_idx + count]))
                deck_idx += count
                progressed = True
            elif state.can_show_or_muck_hole_cards():
                state.show_or_muck_hole_cards(True)
                progressed = True
            elif state.can_kill_hand():
                state.kill_hand()
                progressed = True
            elif state.can_push_chips():
                state.push_chips()
                progressed = True
            elif state.can_pull_chips():
                state.pull_chips()
                progressed = True
            elif state.can_no_operate():
                state.no_operate()
                progressed = True
            if not progressed:
                break

        final_stacks = {
            seat: state.stacks[player_index]
            for player_index, seat in player_index_to_seat.items()
        }
        for seat in range(config.num_seats):
            if seat not in final_stacks:
                final_stacks[seat] = hand_history.initial_stacks_by_seat.get(seat, 0)
        showdown = hand_history.showdown.model_dump(mode="json") if hand_history.showdown else None

        return {
            "final_stacks_by_seat": final_stacks,
            "board_cards": [str(card) for card in state.get_board_cards(0)],
            "showdown": showdown,
            "hand_end_reason": hand_history.hand_end_reason,
            "chip_conservation": sum(final_stacks.values()) == total_chips_target,
            "hand_terminated": not state.status,
            "action_replay_match": final_stacks == hand_history.final_stacks_by_seat,
            "event_seq_monotonic": all(
                left.event_seq < right.event_seq
                for left, right in zip(hand_history.events, hand_history.events[1:])
            ),
        }

    def _replay_advance(self, state: State, deck: list[str], deck_idx: int) -> None:
        iteration_guard = 0
        while state.actor_index is None and state.status:
            iteration_guard += 1
            if iteration_guard > 500:
                raise AssertionError("replay advance exceeded guard limit")
            if state.can_collect_bets():
                state.collect_bets()
            elif state.can_select_runout_count():
                state.select_runout_count(1)
            elif state.can_burn_card():
                state.burn_card(deck[deck_idx])
                deck_idx += 1
            elif state.can_deal_board():
                count = state.board_dealing_count
                state.deal_board("".join(deck[deck_idx : deck_idx + count]))
                deck_idx += count
            elif state.can_show_or_muck_hole_cards():
                state.show_or_muck_hole_cards(True)
            elif state.can_kill_hand():
                state.kill_hand()
            elif state.can_push_chips():
                state.push_chips()
            elif state.can_pull_chips():
                state.pull_chips()
            elif state.can_no_operate():
                state.no_operate()
            else:
                break

    def _deck_index_after_advance(self, state: State, deck: list[str], deck_idx: int) -> int:
        # This helper keeps replay deterministic while avoiding duplicated progression logic.
        # We recompute how many cards are no longer available by comparing with dealable set.
        used = 52 - len(list(state.get_dealable_cards()))
        _ = deck  # keep the signature explicit for future validators.
        return used


def serialize_error(error: EngineRejectedAction, table: TableRuntime) -> SubmitActionResponse:
    view = ViewState.model_validate_json(
        json.dumps(
            {
                "table_id": table.table_id,
                "hand_id": None,
                "session_outcome": table.outcome,
                "seats": [],
                "board_cards": [],
                "pots": [],
                "chips_in_front": {},
                "action_on_seat": None,
                "turn_index": None,
                "action_log": [],
                "server_action_seq": 0,
                "allowed_actions": AllowedActions().model_dump(mode="json"),
                "showdown_payload": None,
                "state_hash": "",
                "invariant_hash": "",
                "speed_label": "1x",
            },
        ),
    )
    return SubmitActionResponse(
        accepted=False,
        error=EngineError(code=error.code, message=error.message),
        view_state=view,
        event_queue_delta=[],
        server_action_seq=0,
    )
