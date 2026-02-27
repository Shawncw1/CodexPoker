from __future__ import annotations

from codexpoker_backend.engine.service import PokerEngineService
from codexpoker_backend.repo.in_memory import InMemoryTableRepository


repository = InMemoryTableRepository()
engine_service = PokerEngineService(repository)
