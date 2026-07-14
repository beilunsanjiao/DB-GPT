"""Value objects returned by the read-only SQL guard and executor."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Rewrite:
    """A deterministic transformation applied before execution."""

    kind: str
    detail: str


@dataclass(frozen=True)
class PreparedSql:
    """A validated SQL statement ready for the database connector."""

    original_sql: str
    sql: str
    dialect: str
    referenced_tables: tuple[str, ...]
    referenced_columns: tuple[str, ...]
    limit: int
    rewrites: tuple[Rewrite, ...]


@dataclass(frozen=True)
class GuardedQueryResult:
    """The database result paired with its validated execution plan."""

    dataframe: Any
    prepared: PreparedSql
