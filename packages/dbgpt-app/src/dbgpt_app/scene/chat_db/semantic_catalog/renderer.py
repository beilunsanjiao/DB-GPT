"""Render governed catalog metadata for a Text2SQL prompt."""

from typing import Iterable

from .models import MetricSpec, TableSpec


def render_catalog_context(
    tables: Iterable[TableSpec],
    metrics: Iterable[MetricSpec],
    default_time_range_days: int,
) -> str:
    """Return compact, deterministic context for database question answering."""

    lines = [
        "Governed semantic catalog:",
        f"- Default time range: last {default_time_range_days} days",
        "- Only the tables and columns below are authorized.",
        "Tables:",
    ]
    for table in tables:
        columns = ", ".join(column.name for column in table.columns)
        lines.append(
            f"- {table.name} (local: {table.physical_name}; "
            f"grain: {table.grain}; time: {table.time_column}): {columns}"
        )

    lines.append("Metrics:")
    for metric in metrics:
        aliases = ", ".join(metric.aliases)
        lines.append(
            f"- {metric.display_name} [{metric.name}] -> "
            f"{metric.table}.{metric.column}; aliases: {aliases}; "
            f"unit: {metric.unit}; direction: {metric.direction}; "
            f"status: {metric.status}; definition: {metric.definition}"
        )
        if metric.note:
            lines.append(f"  note: {metric.note}")
    return "\n".join(lines)
