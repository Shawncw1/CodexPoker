from __future__ import annotations

from codexpoker_backend.engine.models import ClientActionType, TableConfig
from codexpoker_backend.engine.service import PokerEngineService


async def create_started_table(
    engine: PokerEngineService,
    *,
    seed: int = 7,
    starting_stack: int = 10_000,
    num_seats: int = 6,
) -> str:
    table_id = await engine.create_table(
        TableConfig(seed=seed, starting_stack=starting_stack, num_seats=num_seats),
    )
    await engine.start_new_hand(table_id)
    await engine.run_bots_until_human_turn(table_id)
    for _ in range(4):
        view = await engine.get_view_state(table_id)
        if view.hand_id is not None or view.session_outcome.value != "running":
            break
        await engine.start_new_hand(table_id)
        await engine.run_bots_until_human_turn(table_id)
    return table_id


async def play_human_auto_to_next_hand(engine: PokerEngineService, table_id: str) -> int:
    view = await engine.get_view_state(table_id)
    first_hand = view.hand_id
    if first_hand is None:
        raise AssertionError("table has no active hand")

    for loop in range(1, 401):
        view = await engine.get_view_state(table_id)
        if view.hand_id != first_hand:
            return first_hand
        allowed = view.allowed_actions
        seq = await engine.get_server_action_seq(table_id)
        if allowed.can_check:
            action = ClientActionType.CHECK
            amount = None
        elif allowed.can_call:
            action = ClientActionType.CALL
            amount = None
        elif allowed.can_fold:
            action = ClientActionType.FOLD
            amount = None
        elif allowed.can_all_in:
            action = ClientActionType.ALL_IN
            amount = allowed.max_raise_to
        elif allowed.can_raise and allowed.min_raise_to is not None:
            action = ClientActionType.RAISE
            amount = allowed.min_raise_to
        else:
            raise AssertionError("human has no legal action")

        await engine.submit_action(
            table_id=table_id,
            viewer_id="human",
            action=action,
            action_seq=seq + 1,
            idempotency_key=f"auto-{first_hand}-{seq + 1}",
            amount_to=amount,
        )
        await engine.run_bots_until_human_turn(table_id)
        refreshed = await engine.get_view_state(table_id)
        if refreshed.hand_id is None and refreshed.session_outcome.value == "running":
            await engine.start_new_hand(table_id)
            await engine.run_bots_until_human_turn(table_id)
    raise AssertionError(f"did not complete hand {first_hand} within loop guard ({loop})")
