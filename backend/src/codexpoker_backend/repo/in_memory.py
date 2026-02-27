from __future__ import annotations

from codexpoker_backend.engine.internal import TableRuntime
from codexpoker_backend.repo.base import TableRepository


class InMemoryTableRepository(TableRepository):
    def __init__(self) -> None:
        self._tables: dict[str, TableRuntime] = {}

    def create(self, table: TableRuntime) -> None:
        self._tables[table.table_id] = table

    def get(self, table_id: str) -> TableRuntime:
        if table_id not in self._tables:
            raise KeyError(f"table {table_id} not found")
        return self._tables[table_id]

    def all(self) -> list[TableRuntime]:
        return list(self._tables.values())
