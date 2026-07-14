"""Execute only SQL that has passed the read-only guard."""

from typing import Callable

from .guard import ReadOnlySqlGuard
from .models import GuardedQueryResult


class SafeSqlExecutor:
    """A narrow adapter between generated SQL and a database connector."""

    def __init__(
        self,
        guard: ReadOnlySqlGuard,
        execute_to_df: Callable[[str], object],
        dialect: str,
        max_rows: int,
    ):
        self._guard = guard
        self._execute_to_df = execute_to_df
        self._dialect = dialect
        self._max_rows = max_rows

    def execute(self, sql: str) -> GuardedQueryResult:
        prepared = self._guard.prepare(
            sql, dialect=self._dialect, max_rows=self._max_rows
        )
        dataframe = self._execute_to_df(prepared.sql)
        return GuardedQueryResult(dataframe=dataframe, prepared=prepared)
