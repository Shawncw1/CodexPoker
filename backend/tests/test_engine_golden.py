from __future__ import annotations

import pytest

from codexpoker_backend.bots.policy import BotDecision, BotPolicy
from codexpoker_backend.engine.models import ClientActionType, TableConfig
from codexpoker_backend.engine.service import PokerEngineService
from codexpoker_backend.repo.in_memory import InMemoryTableRepository

from .test_utils import create_started_table, play_human_auto_to_next_hand


class AllInBotPolicy(BotPolicy):
    def choose_action(self, *, allowed, stack, rng):  # type: ignore[override]
        if allowed.can_all_in and allowed.max_raise_to is not None:
            return BotDecision(action=ClientActionType.ALL_IN, amount_to=allowed.max_raise_to, think_delay_ms=900)
        if allowed.can_call:
            return BotDecision(action=ClientActionType.CALL, think_delay_ms=650)
        if allowed.can_check:
            return BotDecision(action=ClientActionType.CHECK, think_delay_ms=650)
        if allowed.can_fold:
            return BotDecision(action=ClientActionType.FOLD, think_delay_ms=650)
        return BotDecision(action=ClientActionType.CHECK, think_delay_ms=650)


class PassiveBotPolicy(BotPolicy):
    def choose_action(self, *, allowed, stack, rng):  # type: ignore[override]
        if allowed.can_check:
            return BotDecision(action=ClientActionType.CHECK, think_delay_ms=650)
        if allowed.can_call:
            return BotDecision(action=ClientActionType.CALL, think_delay_ms=650)
        if allowed.can_fold:
            return BotDecision(action=ClientActionType.FOLD, think_delay_ms=650)
        return BotDecision(action=ClientActionType.CHECK, think_delay_ms=650)


@pytest.mark.asyncio
async def test_golden_six_max_hand_replay_consistency(engine: PokerEngineService) -> None:
    table_id = await create_started_table(engine, seed=77)
    completed_hand_id = await play_human_auto_to_next_hand(engine, table_id)

    history = await engine.export_hand_history(table_id, completed_hand_id, mode="debug")
    replay = await engine.replay_hand_history(history)

    assert replay.invariant_checks["chip_conservation"] is True
    assert replay.invariant_checks["hand_terminated"] is True
    assert replay.invariant_checks["action_replay_match"] is True
    assert replay.invariant_checks["event_seq_monotonic"] is True
    expected = {int(k): v for k, v in history["final_stacks_by_seat"].items()}
    assert replay.terminal_state["final_stacks_by_seat"] == expected


@pytest.mark.asyncio
async def test_multiway_all_in_generates_side_pots() -> None:
    engine = PokerEngineService(InMemoryTableRepository(), bot_policy=AllInBotPolicy())
    table_id = await engine.create_table(TableConfig(seed=11, starting_stack=1_000))
    table = engine._repo.get(table_id)  # noqa: SLF001
    table.seats[0]["stack"] = 500
    table.seats[1]["stack"] = 1_200
    table.seats[2]["stack"] = 900
    table.seats[3]["stack"] = 0
    table.seats[4]["stack"] = 0
    table.seats[5]["stack"] = 0

    await engine.start_new_hand(table_id)
    await engine.run_bots_until_human_turn(table_id)
    seq = await engine.get_server_action_seq(table_id)
    view = await engine.get_view_state(table_id)
    assert view.allowed_actions.can_all_in
    await engine.submit_action(
        table_id=table_id,
        viewer_id="human",
        action=ClientActionType.ALL_IN,
        amount_to=view.allowed_actions.max_raise_to,
        action_seq=seq + 1,
        idempotency_key="all-in-human",
    )
    await engine.run_bots_until_human_turn(table_id)

    completed_id = max(engine._repo.get(table_id).completed_hands)  # noqa: SLF001
    history = await engine.export_hand_history(table_id, completed_id, mode="debug")

    assert len(history["pot_breakdown"]) >= 1
    assert sum(pot["amount"] for pot in history["pot_breakdown"]) > 0
    for pot in history["pot_breakdown"]:
        assert pot["amount"] >= 0
        assert set(pot["eligible_seats"]).issuperset({winner["seat"] for winner in pot["winners"]})
    replay = await engine.replay_hand_history(history)
    assert replay.invariant_checks["chip_conservation"] is True
    assert replay.invariant_checks["action_replay_match"] is True


@pytest.mark.asyncio
async def test_min_raise_increment_enforced(engine: PokerEngineService) -> None:
    local_engine = PokerEngineService(InMemoryTableRepository(), bot_policy=PassiveBotPolicy())
    table_id = await create_started_table(local_engine, seed=33)
    view = await local_engine.get_view_state(table_id)
    seq = await local_engine.get_server_action_seq(table_id)
    if not view.allowed_actions.can_raise or view.allowed_actions.min_raise_to is None:
        pytest.skip("seed did not produce a raise-enabled human decision point")

    response = await local_engine.submit_action(
        table_id=table_id,
        viewer_id="human",
        action=ClientActionType.RAISE,
        amount_to=view.allowed_actions.min_raise_to - 1,
        action_seq=seq + 1,
        idempotency_key="bad-min-raise",
    )
    assert response.accepted is False
    assert response.error is not None
    assert response.error.code == "INVALID_SIZING"


@pytest.mark.asyncio
async def test_blind_posting_and_heads_up_action_order() -> None:
    engine = PokerEngineService(InMemoryTableRepository(), bot_policy=AllInBotPolicy())
    table_id = await engine.create_table(TableConfig(seed=5))
    table = engine._repo.get(table_id)  # noqa: SLF001
    table.seats[2]["stack"] = 0
    table.seats[3]["stack"] = 0
    table.seats[4]["stack"] = 0
    table.seats[5]["stack"] = 0
    table.dealer_button_seat = 0

    start = await engine.start_new_hand(table_id)
    view = start.view_state
    event_types = [event.event_type.value for event in start.event_queue[:3]]
    blind_events = [event for event in start.event_queue if event.event_type.value == "POST_BLIND"]
    assert event_types[0] == "HAND_START"
    assert len(blind_events) == 2

    hand_start = next(event for event in start.event_queue if event.event_type.value == "HAND_START")
    payload = hand_start.payload
    assert payload["sb_seat"] == payload["dealer_button_seat"]
    assert payload["bb_seat"] != payload["sb_seat"]
    assert view.action_on_seat == payload["sb_seat"]


@pytest.mark.asyncio
async def test_all_in_short_call_not_reopen_replay_check() -> None:
    engine = PokerEngineService(InMemoryTableRepository(), bot_policy=AllInBotPolicy())
    table_id = await engine.create_table(TableConfig(seed=18))
    table = engine._repo.get(table_id)  # noqa: SLF001
    table.seats[0]["stack"] = 1_000
    table.seats[1]["stack"] = 280
    table.seats[2]["stack"] = 1_000
    table.seats[3]["stack"] = 0
    table.seats[4]["stack"] = 0
    table.seats[5]["stack"] = 0

    await engine.start_new_hand(table_id)
    await engine.run_bots_until_human_turn(table_id)
    seq = await engine.get_server_action_seq(table_id)
    view = await engine.get_view_state(table_id)

    if view.allowed_actions.can_call:
        await engine.submit_action(
            table_id=table_id,
            viewer_id="human",
            action=ClientActionType.CALL,
            action_seq=seq + 1,
            idempotency_key="human-call-short-stack",
        )
    else:
        await engine.submit_action(
            table_id=table_id,
            viewer_id="human",
            action=ClientActionType.CHECK,
            action_seq=seq + 1,
            idempotency_key="human-check-short-stack",
        )

    await engine.run_bots_until_human_turn(table_id)
    if not engine._repo.get(table_id).completed_hands:  # noqa: SLF001
        await play_human_auto_to_next_hand(engine, table_id)
    completed_id = max(engine._repo.get(table_id).completed_hands)  # noqa: SLF001
    history = await engine.export_hand_history(table_id, completed_id, mode="debug")
    replay = await engine.replay_hand_history(history)
    assert replay.invariant_checks["action_replay_match"] is True


@pytest.mark.asyncio
async def test_split_pot_odd_chip_award_goes_left_of_button() -> None:
    found_history = None
    for seed in range(1, 220):
        engine = PokerEngineService(InMemoryTableRepository(), bot_policy=PassiveBotPolicy())
        table_id = await engine.create_table(
            TableConfig(
                seed=seed,
                num_seats=2,
                small_blind=1,
                big_blind=2,
                ante=1,
                starting_stack=80,
            ),
        )
        await engine.start_new_hand(table_id)
        await engine.run_bots_until_human_turn(table_id)
        completed_id = await play_human_auto_to_next_hand(engine, table_id)
        history = await engine.export_hand_history(table_id, completed_id, mode="debug")
        if any(pot["odd_chip_award"] for pot in history["pot_breakdown"]):
            found_history = history
            break

    if found_history is None:
        pytest.skip("could not find odd-chip split scenario in search window")
    odd_rows = [pot for pot in found_history["pot_breakdown"] if pot["odd_chip_award"] is not None]
    assert odd_rows, "expected odd-chip payout"
    for pot in odd_rows:
        odd = pot["odd_chip_award"]
        assert odd["seat"] == found_history["bb_seat"]
