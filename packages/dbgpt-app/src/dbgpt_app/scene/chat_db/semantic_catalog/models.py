"""Domain models for the ChatDB semantic catalog."""

from dataclasses import dataclass
from typing import Any, Mapping, Tuple


@dataclass(frozen=True)
class ColumnSpec:
    """A queryable column exposed by a catalog table."""

    name: str
    data_type: str
    description: str = ""


@dataclass(frozen=True)
class TableSpec:
    """A logical production table and its local physical mapping."""

    name: str
    physical_name: str
    layer: str
    time_column: str
    grain: str
    columns: Tuple[ColumnSpec, ...]

    @property
    def column_names(self) -> frozenset[str]:
        return frozenset(column.name for column in self.columns)


@dataclass(frozen=True)
class MetricSpec:
    """A governed business metric available to natural-language queries."""

    name: str
    display_name: str
    aliases: Tuple[str, ...]
    table: str
    column: str
    unit: str
    direction: str
    definition: str
    definition_source: str
    status: str
    note: str = ""


@dataclass(frozen=True)
class CatalogDocument:
    """Validated configuration used to construct a semantic catalog."""

    version: int
    domain: str
    default_time_range_days: int
    tables: Tuple[TableSpec, ...]
    metrics: Tuple[MetricSpec, ...]
    metadata: Mapping[str, Any]
