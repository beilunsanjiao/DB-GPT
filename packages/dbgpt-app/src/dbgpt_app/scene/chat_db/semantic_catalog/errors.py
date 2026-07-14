"""Errors raised while loading or querying a semantic catalog."""


class SemanticCatalogError(ValueError):
    """Base error for invalid or unresolved semantic catalog data."""


class CatalogValidationError(SemanticCatalogError):
    """Raised when catalog configuration is internally inconsistent."""


class UnknownMetricError(SemanticCatalogError):
    """Raised when a metric name or alias cannot be resolved."""


class UnknownTableError(SemanticCatalogError):
    """Raised when a table name cannot be resolved."""
