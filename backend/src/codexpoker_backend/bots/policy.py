from __future__ import annotations

import random
from dataclasses import dataclass

from codexpoker_backend.engine.models import AllowedActions, ClientActionType


@dataclass
class BotDecision:
    action: ClientActionType
    amount_to: int | None = None
    think_delay_ms: int = 900


class BotPolicy:
    def choose_action(
        self,
        *,
        allowed: AllowedActions,
        stack: int,
        rng: random.Random,
    ) -> BotDecision:
        aggressive_window = stack > 0 and rng.random() < 0.14

        if (
            allowed.can_raise
            and aggressive_window
            and allowed.max_raise_to is not None
            and allowed.call_amount <= 200
        ):
            target = self._sample_raise_target(allowed, rng)
            return BotDecision(
                action=ClientActionType.RAISE,
                amount_to=target,
                think_delay_ms=rng.randint(900, 1400),
            )

        if allowed.can_bet and aggressive_window and allowed.max_raise_to is not None:
            target = self._sample_raise_target(allowed, rng)
            return BotDecision(
                action=ClientActionType.BET,
                amount_to=target,
                think_delay_ms=rng.randint(900, 1400),
            )

        if allowed.can_call and allowed.call_amount <= max(200, stack // 6):
            return BotDecision(
                action=ClientActionType.CALL,
                think_delay_ms=rng.randint(650, 1100),
            )

        if allowed.can_check:
            return BotDecision(
                action=ClientActionType.CHECK,
                think_delay_ms=rng.randint(650, 1100),
            )

        if allowed.can_call:
            if allowed.call_amount > max(450, stack // 3) and allowed.can_fold and rng.random() < 0.55:
                return BotDecision(
                    action=ClientActionType.FOLD,
                    think_delay_ms=rng.randint(650, 1100),
                )
            if rng.random() < 0.35:
                return BotDecision(
                    action=ClientActionType.CALL,
                    think_delay_ms=rng.randint(650, 1100),
                )
            if allowed.can_fold:
                return BotDecision(
                    action=ClientActionType.FOLD,
                    think_delay_ms=rng.randint(650, 1100),
                )

        if allowed.can_all_in:
            return BotDecision(
                action=ClientActionType.ALL_IN,
                amount_to=allowed.max_raise_to,
                think_delay_ms=rng.randint(900, 1400),
            )

        if allowed.can_fold:
            return BotDecision(
                action=ClientActionType.FOLD,
                think_delay_ms=rng.randint(650, 1100),
            )

        if allowed.can_call:
            return BotDecision(
                action=ClientActionType.CALL,
                think_delay_ms=rng.randint(650, 1100),
            )

        return BotDecision(action=ClientActionType.CHECK, think_delay_ms=650)

    @staticmethod
    def _sample_raise_target(allowed: AllowedActions, rng: random.Random) -> int:
        min_to = allowed.min_raise_to or allowed.min_bet_to
        max_to = allowed.max_raise_to
        if min_to is None or max_to is None:
            raise ValueError("raise target requested without constraints")
        if min_to >= max_to:
            return max_to

        anchors = [
            min_to,
            max(min_to, min_to + (max_to - min_to) // 3),
            max(min_to, min_to + (max_to - min_to) // 2),
            max_to,
        ]
        return anchors[rng.randrange(0, len(anchors))]
