"""AST-based read-only SQL guard for governed ChatDB queries."""

from collections import OrderedDict

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError
from sqlglot.optimizer.scope import Scope, traverse_scope

from dbgpt_app.scene.chat_db.semantic_catalog import SemanticCatalog, TableSpec

from .errors import SqlGuardError
from .models import PreparedSql, Rewrite
from .policy import SqlGuardPolicy

_SUPPORTED_DIALECTS = {"sqlite"}
_ALLOWED_FUNCTIONS = {
    "ABS",
    "AVG",
    "CAST",
    "COALESCE",
    "COUNT",
    "DATE",
    "DATETIME",
    "IFNULL",
    "LOWER",
    "MAX",
    "MIN",
    "NULLIF",
    "ROUND",
    "STRFTIME",
    "SUBSTRING",
    "SUM",
    "UPPER",
}
_FORBIDDEN_NODE_NAMES = {
    "Alter",
    "Analyze",
    "Attach",
    "Command",
    "Commit",
    "Copy",
    "Create",
    "Delete",
    "Detach",
    "Drop",
    "Grant",
    "Insert",
    "Into",
    "LoadData",
    "Lock",
    "Merge",
    "Pragma",
    "Reindex",
    "Revoke",
    "Rollback",
    "Set",
    "Transaction",
    "TruncateTable",
    "Update",
    "Use",
    "Vacuum",
}
_SYSTEM_TABLES = {
    "sqlite_master",
    "sqlite_schema",
    "sqlite_temp_master",
    "sqlite_temp_schema",
}


class ReadOnlySqlGuard:
    """Validate, authorize, rewrite, and serialize a single read-only query."""

    def __init__(
        self,
        catalog: SemanticCatalog,
        policy: SqlGuardPolicy | None = None,
    ):
        self._catalog = catalog
        self._policy = policy or SqlGuardPolicy()

    def prepare(self, sql: str, *, dialect: str, max_rows: int) -> PreparedSql:
        if dialect not in _SUPPORTED_DIALECTS:
            self._reject("UNSUPPORTED_DIALECT", f"Unsupported SQL dialect: {dialect}")
        if max_rows <= 0:
            self._reject("INVALID_LIMIT", "Maximum rows must be positive")
        if not sql or not sql.strip():
            self._reject("EMPTY_SQL", "SQL is empty")
        if "\x00" in sql or any(
            ord(character) < 32 and character not in "\t\r\n" for character in sql
        ):
            self._reject("INVALID_SQL_TEXT", "SQL contains control characters")

        try:
            statements = sqlglot.parse(sql, read=dialect)
        except (ParseError, TokenError) as exc:
            raise SqlGuardError("PARSE_ERROR", "SQL could not be parsed") from exc
        statements = [statement for statement in statements if statement is not None]
        if len(statements) != 1:
            self._reject("MULTI_STATEMENT", "Exactly one SQL statement is required")

        tree = statements[0]
        if not isinstance(tree, exp.Select):
            self._reject("NON_QUERY_STATEMENT", "Only SELECT queries are allowed")
        if self._policy.reject_comments and any(node.comments for node in tree.walk()):
            self._reject("COMMENT_FORBIDDEN", "SQL comments are not allowed")
        self._validate_shape(tree)

        base_sources, referenced_tables = self._bind_base_tables(tree)
        referenced_columns = self._validate_columns(tree, base_sources)
        self._validate_functions(tree)

        rewritten = tree.copy()
        rewrite_sources, _ = self._bind_base_tables(rewritten)
        rewrites = self._rewrite_tables(rewrite_sources)
        rewritten, limit, limit_rewrite = self._apply_limit(rewritten, max_rows)
        if limit_rewrite is not None:
            rewrites.append(limit_rewrite)

        executable_sql = rewritten.sql(dialect=dialect)
        self._validate_physical_closure(executable_sql, dialect)
        return PreparedSql(
            original_sql=sql,
            sql=executable_sql,
            dialect=dialect,
            referenced_tables=tuple(referenced_tables),
            referenced_columns=tuple(referenced_columns),
            limit=limit,
            rewrites=tuple(rewrites),
        )

    def _validate_shape(self, tree: exp.Expression) -> None:
        if tree.args.get("offset") is not None:
            self._reject("UNSUPPORTED_QUERY_SHAPE", "OFFSET is not supported")
        for scope in traverse_scope(tree):
            if scope.is_correlated_subquery and any(
                column.table for column in scope.external_columns
            ):
                self._reject(
                    "UNSUPPORTED_QUERY_SHAPE", "Correlated subqueries are not supported"
                )
            if isinstance(scope.expression, exp.SetOperation):
                self._reject(
                    "UNSUPPORTED_QUERY_SHAPE", "Set operations are not supported"
                )
            if isinstance(scope.expression.parent, exp.Subquery) and isinstance(
                scope.expression.parent.parent, (exp.From, exp.Join)
            ):
                self._reject(
                    "UNSUPPORTED_QUERY_SHAPE", "Derived tables are not supported"
                )
        for node in tree.walk():
            if type(node).__name__ in _FORBIDDEN_NODE_NAMES:
                self._reject(
                    "UNSUPPORTED_QUERY_SHAPE",
                    f"Unsupported SQL capability: {type(node).__name__}",
                )
            if isinstance(node, exp.With) and node.args.get("recursive"):
                self._reject(
                    "UNSUPPORTED_QUERY_SHAPE", "Recursive CTEs are not allowed"
                )
            if isinstance(node, (exp.Window, exp.Qualify, exp.Pivot, exp.Unnest)):
                self._reject(
                    "UNSUPPORTED_QUERY_SHAPE",
                    f"Unsupported SQL capability: {type(node).__name__}",
                )
            if isinstance(node, exp.Join):
                if (
                    node.args.get("method") == "NATURAL"
                    or node.args.get("using")
                    or node.args.get("kind") == "CROSS"
                    or node.args.get("side") in {"RIGHT", "FULL"}
                ):
                    self._reject(
                        "UNSUPPORTED_QUERY_SHAPE",
                        "Only INNER and LEFT joins with ON are supported",
                    )
                if node.args.get("on") is None:
                    self._reject(
                        "UNSUPPORTED_QUERY_SHAPE", "JOIN requires an ON condition"
                    )
            if isinstance(node, exp.Table) and not isinstance(
                node.this, exp.Identifier
            ):
                self._reject(
                    "UNSUPPORTED_QUERY_SHAPE", "Dynamic table sources are not allowed"
                )
        if sum(1 for _ in tree.find_all(exp.Join)) > self._policy.max_joins:
            self._reject("UNSUPPORTED_QUERY_SHAPE", "Query has too many joins")
        if self._policy.reject_projection_star:
            for select in tree.find_all(exp.Select):
                for projection in select.expressions:
                    if isinstance(projection, exp.Star) or (
                        isinstance(projection, exp.Column)
                        and isinstance(projection.this, exp.Star)
                    ):
                        self._reject(
                            "STAR_NOT_ALLOWED", "Projection stars are not allowed"
                        )

    def _bind_base_tables(
        self, tree: exp.Expression
    ) -> tuple[list[tuple[exp.Table, TableSpec]], list[str]]:
        bound: list[tuple[exp.Table, TableSpec]] = []
        referenced = OrderedDict()
        scopes = list(traverse_scope(tree))
        if not scopes:
            self._reject("UNSUPPORTED_QUERY_SHAPE", "Query scope could not be resolved")
        for scope in scopes:
            aliases = set()
            for source in scope.tables:
                alias = source.alias_or_name.casefold()
                if alias in aliases:
                    self._reject(
                        "INVALID_QUALIFIER", f"Duplicate source alias: {alias}"
                    )
                aliases.add(alias)
            for _, source in scope.selected_sources.values():
                if isinstance(source, exp.Table):
                    table = self._resolve_logical_table(source)
                    bound.append((source, table))
                    referenced.setdefault(table.name, None)
                elif not isinstance(source, Scope):
                    self._reject("UNSUPPORTED_QUERY_SHAPE", "Unknown query source type")
        return bound, list(referenced)

    def _resolve_logical_table(self, table: exp.Table) -> TableSpec:
        if table.catalog:
            self._reject("UNKNOWN_TABLE", "Catalog-qualified tables are not allowed")
        if not table.db:
            self._reject(
                "UNKNOWN_TABLE", "Governed queries must use schema-qualified tables"
            )
        logical_name = f"{table.db}.{table.name}"
        if table.name.casefold() in _SYSTEM_TABLES:
            self._reject("UNKNOWN_TABLE", "System tables are not allowed")
        try:
            return self._catalog.resolve_table(logical_name)
        except ValueError as exc:
            raise SqlGuardError("UNKNOWN_TABLE", "Table is not authorized") from exc

    def _validate_columns(
        self,
        tree: exp.Expression,
        base_sources: list[tuple[exp.Table, TableSpec]],
    ) -> list[str]:
        table_specs = {id(node): table for node, table in base_sources}
        referenced = OrderedDict()
        for scope in traverse_scope(tree):
            source_specs: dict[str, tuple[TableSpec | None, frozenset[str]]] = {}
            for alias, (_, source) in scope.selected_sources.items():
                if isinstance(source, exp.Table):
                    table = table_specs.get(id(source))
                    if table is None:
                        self._reject("UNKNOWN_TABLE", "Table binding was lost")
                    source_specs[alias] = (table, table.column_names)
                elif isinstance(source, Scope):
                    outputs = frozenset(
                        name.casefold()
                        for name in source.expression.named_selects
                        if name and name != "*"
                    )
                    if not outputs:
                        self._reject(
                            "UNSUPPORTED_QUERY_SHAPE",
                            "Derived source output could not be resolved",
                        )
                    source_specs[alias] = (None, outputs)

            projection_aliases = {
                projection.alias.casefold()
                for projection in getattr(scope.expression, "expressions", [])
                if projection.alias
            }
            for column in scope.columns:
                if isinstance(column.this, exp.Star):
                    continue
                name = column.name.casefold()
                if column.table:
                    source = source_specs.get(column.table)
                    if source is None:
                        self._reject(
                            "INVALID_QUALIFIER",
                            f"Unknown column qualifier: {column.table}",
                        )
                    table, columns = source
                    if name not in {item.casefold() for item in columns}:
                        self._reject("UNKNOWN_COLUMN", f"Unknown column: {column.name}")
                    if table is not None:
                        referenced.setdefault(f"{table.name}.{column.name}", None)
                    continue

                candidates = [
                    (table, columns)
                    for table, columns in source_specs.values()
                    if name in {item.casefold() for item in columns}
                ]
                if len(candidates) == 1:
                    table = candidates[0][0]
                    if table is not None:
                        referenced.setdefault(f"{table.name}.{column.name}", None)
                elif len(candidates) > 1:
                    self._reject("AMBIGUOUS_COLUMN", f"Ambiguous column: {column.name}")
                elif name in projection_aliases and isinstance(
                    column.find_ancestor(exp.Order, exp.Group, exp.Having),
                    (exp.Order, exp.Group, exp.Having),
                ):
                    continue
                else:
                    self._reject("UNKNOWN_COLUMN", f"Unknown column: {column.name}")
        return list(referenced)

    def _validate_functions(self, tree: exp.Expression) -> None:
        for function in tree.find_all(exp.Anonymous):
            name = function.name.upper()
            if name not in _ALLOWED_FUNCTIONS:
                self._reject("FUNCTION_NOT_ALLOWED", f"Function is not allowed: {name}")
        for function in tree.find_all(exp.Func):
            if isinstance(function, exp.Anonymous):
                continue
            module = type(function).__module__
            if module != exp.__name__:
                continue
            name = function.sql_name().upper()
            if name not in _ALLOWED_FUNCTIONS and name not in {
                "AND",
                "OR",
                "IF",
            }:
                self._reject("FUNCTION_NOT_ALLOWED", f"Function is not allowed: {name}")

    def _rewrite_tables(
        self, bound: list[tuple[exp.Table, TableSpec]]
    ) -> list[Rewrite]:
        rewrites = []
        for node, table in bound:
            original = node.sql()
            if not node.alias:
                node.set(
                    "alias",
                    exp.TableAlias(this=exp.Identifier(this=node.name, quoted=False)),
                )
            node.set("catalog", None)
            node.set("db", exp.Identifier(this="main", quoted=False))
            node.set("this", exp.Identifier(this=table.physical_name, quoted=False))
            rewrites.append(
                Rewrite(
                    "table_mapping",
                    f"{original} -> main.{table.physical_name}",
                )
            )
        return rewrites

    def _apply_limit(
        self, tree: exp.Query, max_rows: int
    ) -> tuple[exp.Query, int, Rewrite | None]:
        limit_node = tree.args.get("limit")
        if limit_node is None:
            return (
                tree.limit(max_rows),
                max_rows,
                Rewrite("limit_added", f"LIMIT {max_rows}"),
            )
        expression = limit_node.expression
        if not isinstance(expression, exp.Literal) or expression.is_string:
            self._reject("INVALID_LIMIT", "LIMIT must be an integer literal")
        try:
            requested = int(expression.this)
        except (TypeError, ValueError) as exc:
            raise SqlGuardError(
                "INVALID_LIMIT", "LIMIT must be an integer literal"
            ) from exc
        if requested < 0:
            self._reject("INVALID_LIMIT", "LIMIT cannot be negative")
        if requested <= max_rows:
            return tree, requested, None
        tree.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
        return tree, max_rows, Rewrite("limit_reduced", f"{requested} -> {max_rows}")

    def _validate_physical_closure(self, sql: str, dialect: str) -> None:
        try:
            tree = sqlglot.parse_one(sql, read=dialect)
        except ParseError as exc:
            raise SqlGuardError(
                "INTERNAL_REWRITE_ERROR", "Rewritten SQL could not be parsed"
            ) from exc
        cte_names = {cte.alias_or_name.casefold() for cte in tree.find_all(exp.CTE)}
        allowed = {name.casefold() for name in self._catalog.allowed_physical_tables}
        for table in tree.find_all(exp.Table):
            if not table.db and table.name.casefold() in cte_names:
                continue
            if table.db.casefold() != "main" or table.name.casefold() not in allowed:
                self._reject(
                    "INTERNAL_REWRITE_ERROR",
                    "Rewritten SQL escaped the physical policy",
                )

    @staticmethod
    def _reject(code: str, message: str):
        raise SqlGuardError(code, message)
