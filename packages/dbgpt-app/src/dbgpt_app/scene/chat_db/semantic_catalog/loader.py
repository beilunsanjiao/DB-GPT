"""YAML loader for governed ChatDB semantic catalogs."""

from pathlib import Path
from typing import Any, Mapping

import yaml

from .errors import CatalogValidationError
from .models import CatalogDocument, ColumnSpec, MetricSpec, TableSpec


def _required(mapping: Mapping[str, Any], key: str, context: str) -> Any:
    value = mapping.get(key)
    if value is None or value == "":
        raise CatalogValidationError(f"Missing {key!r} in {context}")
    return value


def load_catalog_document(path: str | Path) -> CatalogDocument:
    """Load a catalog YAML file into immutable domain models."""

    catalog_path = Path(path)
    try:
        raw = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CatalogValidationError("Unable to read semantic catalog") from exc
    except yaml.YAMLError as exc:
        raise CatalogValidationError("Invalid semantic catalog YAML") from exc

    if not isinstance(raw, dict):
        raise CatalogValidationError("Catalog root must be a mapping")

    raw_tables = raw.get("tables")
    raw_metrics = raw.get("metrics")
    if not isinstance(raw_tables, list) or not raw_tables:
        raise CatalogValidationError("Catalog must define at least one table")
    if not isinstance(raw_metrics, list) or not raw_metrics:
        raise CatalogValidationError("Catalog must define at least one metric")

    tables = []
    for index, item in enumerate(raw_tables):
        context = f"tables[{index}]"
        if not isinstance(item, dict):
            raise CatalogValidationError(f"{context} must be a mapping")
        raw_columns = item.get("columns")
        if not isinstance(raw_columns, list) or not raw_columns:
            raise CatalogValidationError(f"{context} must define columns")
        columns = tuple(
            ColumnSpec(
                name=str(_required(column, "name", f"{context}.columns")),
                data_type=str(_required(column, "type", f"{context}.columns")),
                description=str(column.get("description", "")),
            )
            for column in raw_columns
        )
        tables.append(
            TableSpec(
                name=str(_required(item, "name", context)),
                physical_name=str(_required(item, "physical_name", context)),
                layer=str(_required(item, "layer", context)),
                time_column=str(_required(item, "time_column", context)),
                grain=str(_required(item, "grain", context)),
                columns=columns,
            )
        )

    metrics = []
    for index, item in enumerate(raw_metrics):
        context = f"metrics[{index}]"
        if not isinstance(item, dict):
            raise CatalogValidationError(f"{context} must be a mapping")
        aliases = item.get("aliases", [])
        if not isinstance(aliases, list):
            raise CatalogValidationError(f"{context}.aliases must be a list")
        metrics.append(
            MetricSpec(
                name=str(_required(item, "name", context)),
                display_name=str(_required(item, "display_name", context)),
                aliases=tuple(str(alias) for alias in aliases),
                table=str(_required(item, "table", context)),
                column=str(_required(item, "column", context)),
                unit=str(item.get("unit", "")),
                direction=str(item.get("direction", "neutral")),
                definition=str(_required(item, "definition", context)),
                definition_source=str(_required(item, "definition_source", context)),
                status=str(_required(item, "status", context)),
                note=str(item.get("note", "")),
            )
        )

    metadata = raw.get("metadata", {})
    if not isinstance(metadata, dict):
        raise CatalogValidationError("metadata must be a mapping")

    return CatalogDocument(
        version=int(raw.get("version", 1)),
        domain=str(_required(raw, "domain", "catalog")),
        default_time_range_days=int(raw.get("default_time_range_days", 14)),
        tables=tuple(tables),
        metrics=tuple(metrics),
        metadata=metadata,
    )
