from __future__ import annotations

import pytest

from codexpoker_backend.engine.models import ClientActionType
from codexpoker_backend.engine.service import PokerEngineService

from .test_utils import create_started_table, play_human_auto_to_next_hand


@pytest.mark.asyncio
async def test_hole_card_masking_pre_showdown(engine: PokerEngineService) -> None:
    table_id = await create_started_table(engine, seed=91)
    view = await engine.get_view_state(table_id)

    for seat in view.seats:
        if seat.seat_id == 0:
            assert all(card is not None for card in seat.cards)
        else:
            assert seat.cards == [None, None]


@pytest.mark.asyncio
async def test_showdown_reveals_and_sorted_rows(engine: PokerEngineService) -> None:
    table_id = await create_started_table(engine, seed=123)
    completed_hand = await play_human_auto_to_next_hand(engine, table_id)

    history = await engine.export_hand_history(table_id, completed_hand, mode="debug")
    showdown = history.get("showdown")
    if showdown is None:
        pytest.skip("seed produced foldout hand; showdown ordering check skipped")

    winners = showdown["winners"]
    losers = showdown["losers"]
    assert winners == sorted(winners, key=lambda row: row["amount_won"], reverse=True)
    assert losers == sorted(losers, key=lambda row: row["hand_rank_value"], reverse=True)
    for row in winners + losers:
        assert len(row["hole_cards"]) == 2
        assert row["best_hand_name"]


@pytest.mark.asyncio
async def test_idempotency_same_key_returns_cached_response(engine: PokerEngineService) -> None:
    table_id = await create_started_table(engine, seed=17)
    view = await engine.get_view_state(table_id)
    seq = await engine.get_server_action_seq(table_id)
    if view.allowed_actions.can_check:
        action = ClientActionType.CHECK
        amount = None
    elif view.allowed_actions.can_call:
        action = ClientActionType.CALL
        amount = None
    else:
        action = ClientActionType.FOLD
        amount = None

    first = await engine.submit_action(
        table_id=table_id,
        viewer_id="human",
        action=action,
        amount_to=amount,
        action_seq=seq + 1,
        idempotency_key="same-key",
    )
    second = await engine.submit_action(
        table_id=table_id,
        viewer_id="human",
        action=action,
        amount_to=amount,
        action_seq=seq + 1,
        idempotency_key="same-key",
    )
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
