from __future__ import annotations

from abc import ABC, abstractmethod

from codexpoker_backend.engine.internal import TableRuntime


class TableRepository(ABC):
    @abstractmethod
    def create(self, table: TableRuntime) -> None:
        raise NotImplementedError

    @abstractmethod
    def get(self, table_id: str) -> TableRuntime:
        raise NotImplementedError

    @abstractmethod
    def all(self) -> list[TableRuntime]:
        raise NotImplementedError
