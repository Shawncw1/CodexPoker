from __future__ import annotations

import pytest

from codexpoker_backend.engine.service import PokerEngineService
from codexpoker_backend.repo.in_memory import InMemoryTableRepository


@pytest.fixture
def engine() -> PokerEngineService:
    return PokerEngineService(InMemoryTableRepository())
