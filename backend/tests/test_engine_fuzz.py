from __future__ import annotations

import random

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from codexpoker_backend.engine.models import ClientActionType
from codexpoker_backend.engine.service import PokerEngineService

from .test_utils import create_started_table


@pytest.mark.asyncio
@settings(max_examples=15, suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None)
@given(seed=st.integers(min_value=1, max_value=50_000))
async def test_random_legal_action_fuzz(engine: PokerEngineService, seed: int) -> None:
    table_id = await create_started_table(engine, seed=seed)
    rng = random.Random(seed)
    completed = 0

    while completed < 3:
        view = await engine.get_view_state(table_id)
        seq = await engine.get_server_action_seq(table_id)
        first_hand = view.hand_id
        if first_hand is None:
            break

        allowed = view.allowed_actions
        options: list[tuple[ClientActionType, int | None]] = []
        if allowed.can_check:
            options.append((ClientActionType.CHECK, None))
        if allowed.can_call:
            options.append((ClientActionType.CALL, None))
        if allowed.can_fold:
            options.append((ClientActionType.FOLD, None))
        if allowed.can_raise and allowed.min_raise_to is not None:
            low = allowed.min_raise_to
            high = allowed.max_raise_to or low
            choice = low if low == high else rng.randint(low, high)
            options.append((ClientActionType.RAISE, choice))
        if allowed.can_bet and allowed.min_bet_to is not None:
            low = allowed.min_bet_to
            high = allowed.max_raise_to or low
            choice = low if low == high else rng.randint(low, high)
            options.append((ClientActionType.BET, choice))
        if allowed.can_all_in and allowed.max_raise_to is not None:
            options.append((ClientActionType.ALL_IN, allowed.max_raise_to))

        assert options, "human had no legal actions"
        action, amount = rng.choice(options)

        response = await engine.submit_action(
            table_id=table_id,
            viewer_id="human",
            action=action,
            amount_to=amount,
            action_seq=seq + 1,
            idempotency_key=f"fuzz-{seed}-{seq + 1}",
        )
        assert response.accepted is True
        await engine.run_bots_until_human_turn(table_id)
        view_after = await engine.get_view_state(table_id)
        if view_after.hand_id != first_hand:
            completed += 1
            history = await engine.export_hand_history(table_id, first_hand, mode="debug")
            replay = await engine.replay_hand_history(history)
            assert replay.invariant_checks["chip_conservation"] is True
            assert replay.invariant_checks["hand_terminated"] is True
            assert replay.invariant_checks["action_replay_match"] is True
