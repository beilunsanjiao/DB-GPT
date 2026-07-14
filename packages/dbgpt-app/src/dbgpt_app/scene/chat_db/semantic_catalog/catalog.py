"""Validated semantic catalog for governed ChatDB queries."""

from pathlib import Path
from types import MappingProxyType
from typing import Dict, Iterable

from .errors import CatalogValidationError, UnknownMetricError, UnknownTableError
from .loader import load_catalog_document
from .models import CatalogDocument, MetricSpec, TableSpec
from .renderer import render_catalog_context


def _normalize_identifier(value: str) -> str:
    return value.strip().strip('`"[]').casefold()


class SemanticCatalog:
    """Immutable index of queryable tables, columns, and business metrics."""

    def __init__(self, document: CatalogDocument):
        self._document = document
        self._tables = self._index_tables(document.tables)
        self._physical_tables = self._index_physical_tables(document.tables)
        self._metrics = self._index_metrics(document.metrics)
        self._validate_metrics(document.metrics)

    @classmethod
    def load(cls, path: str | Path) -> "SemanticCatalog":
        return cls(load_catalog_document(path))

    @property
    def domain(self) -> str:
        return self._document.domain

    @property
    def tables(self) -> tuple[TableSpec, ...]:
        return self._document.tables

    @property
    def metrics(self) -> tuple[MetricSpec, ...]:
        return self._document.metrics

    @property
    def default_time_range_days(self) -> int:
        return self._document.default_time_range_days

    @property
    def allowed_tables(self) -> frozenset[str]:
        return frozenset(table.name for table in self.tables)

    @property
    def allowed_physical_tables(self) -> frozenset[str]:
        return frozenset(table.physical_name for table in self.tables)

    @property
    def allowed_columns(self):
        return MappingProxyType(
            {table.name: table.column_names for table in self.tables}
        )

    def resolve_table(self, name: str) -> TableSpec:
        normalized = _normalize_identifier(name)
        table = self._tables.get(normalized) or self._physical_tables.get(normalized)
        if table is None:
            raise UnknownTableError(f"Unknown catalog table: {name}")
        return table

    def resolve_metric(self, name_or_alias: str) -> MetricSpec:
        metric = self._metrics.get(_normalize_identifier(name_or_alias))
        if metric is None:
            raise UnknownMetricError(f"Unknown catalog metric: {name_or_alias}")
        return metric

    def render_prompt_context(self, metric_names: Iterable[str] | None = None) -> str:
        metrics = self.metrics
        if metric_names is not None:
            metrics = tuple(self.resolve_metric(name) for name in metric_names)
        return render_catalog_context(
            self.tables, metrics, self.default_time_range_days
        )

    @staticmethod
    def _index_tables(tables: tuple[TableSpec, ...]) -> Dict[str, TableSpec]:
        index: Dict[str, TableSpec] = {}
        for table in tables:
            normalized = _normalize_identifier(table.name)
            if normalized in index:
                raise CatalogValidationError(f"Duplicate table name: {table.name}")
            column_names = [_normalize_identifier(name) for name in table.column_names]
            if len(column_names) != len(set(column_names)):
                raise CatalogValidationError(f"Duplicate column in table {table.name}")
            if _normalize_identifier(table.time_column) not in set(column_names):
                raise CatalogValidationError(
                    f"Unknown time column {table.time_column} in {table.name}"
                )
            index[normalized] = table
        return index

    @staticmethod
    def _index_physical_tables(
        tables: tuple[TableSpec, ...],
    ) -> Dict[str, TableSpec]:
        index: Dict[str, TableSpec] = {}
        for table in tables:
            normalized = _normalize_identifier(table.physical_name)
            if normalized in index:
                raise CatalogValidationError(
                    f"Duplicate physical table name: {table.physical_name}"
                )
            index[normalized] = table
        return index

    @staticmethod
    def _index_metrics(metrics: tuple[MetricSpec, ...]) -> Dict[str, MetricSpec]:
        index: Dict[str, MetricSpec] = {}
        for metric in metrics:
            names = (metric.name, metric.display_name, *metric.aliases)
            for name in names:
                normalized = _normalize_identifier(name)
                if not normalized:
                    raise CatalogValidationError(
                        f"Metric {metric.name} contains an empty alias"
                    )
                existing = index.get(normalized)
                if existing is not None and existing != metric:
                    raise CatalogValidationError(f"Duplicate metric alias: {name}")
                index[normalized] = metric
        return index

    def _validate_metrics(self, metrics: tuple[MetricSpec, ...]) -> None:
        allowed_statuses = {"verified", "sql_authoritative", "semantic_uncertain"}
        for metric in metrics:
            table = self.resolve_table(metric.table)
            if _normalize_identifier(metric.column) not in {
                _normalize_identifier(name) for name in table.column_names
            }:
                raise CatalogValidationError(
                    f"Metric {metric.name} references unknown column "
                    f"{metric.table}.{metric.column}"
                )
            if metric.status not in allowed_statuses:
                raise CatalogValidationError(
                    f"Metric {metric.name} has unsupported status {metric.status}"
                )
