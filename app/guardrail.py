"""SQL validation and read-only execution for generated CloudNova queries."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb
from sqlglot import exp, parse
from sqlglot.errors import ParseError


EXTERNAL_FUNCTIONS = {
    "delta_scan",
    "excel_scan",
    "httpfs",
    "iceberg_scan",
    "parquet_scan",
    "postgres_scan",
    "read_blob",
    "read_csv",
    "read_csv_auto",
    "read_json",
    "read_json_auto",
    "read_ndjson",
    "read_parquet",
    "sqlite_scan",
}


class GuardrailError(ValueError):
    """A user-visible generated-SQL validation failure."""


@dataclass(frozen=True)
class ValidatedQuery:
    sql: str
    views_used: list[str]


def _function_name(function: exp.Func) -> str:
    name = function.sql_name()
    if name == "ANONYMOUS":
        name = getattr(function, "name", "")
    return str(name).lower()


def validate_sql(
    sql: str, allowed_views: set[str], max_rows: int
) -> ValidatedQuery:
    """Validate one read-only semantic-view SELECT and enforce its row limit."""
    if not sql or not sql.strip():
        raise GuardrailError("Generated SQL is empty.")
    if max_rows <= 0:
        raise GuardrailError("Configured query row limit must be positive.")

    try:
        statements = [statement for statement in parse(sql, read="duckdb") if statement]
    except ParseError as error:
        raise GuardrailError(f"Generated SQL could not be parsed: {error}") from error
    if len(statements) != 1:
        raise GuardrailError("Exactly one SQL statement is allowed.")

    statement = statements[0]
    if not isinstance(statement, exp.Select):
        raise GuardrailError("Only a read-only SELECT or WITH ... SELECT is allowed.")
    if statement.find(exp.Into):
        raise GuardrailError("SELECT INTO is not allowed.")

    for cte in statement.find_all(exp.CTE):
        if not isinstance(cte.this, exp.Select):
            raise GuardrailError("Every CTE must contain a read-only SELECT.")
    cte_names = {cte.alias_or_name.lower() for cte in statement.find_all(exp.CTE)}

    for function in statement.find_all(exp.Func):
        if _function_name(function) in EXTERNAL_FUNCTIONS:
            raise GuardrailError("External-access SQL functions are not allowed.")

    allowed_by_lower = {name.lower(): name for name in allowed_views}
    referenced: set[str] = set()
    for table in statement.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            raise GuardrailError("Table functions are not allowed.")
        if table.catalog or table.db:
            raise GuardrailError("Qualified table or view names are not allowed.")
        name = table.name.lower()
        if name in cte_names:
            continue
        if name not in allowed_by_lower:
            raise GuardrailError(f"Table or view {table.name!r} is not allowlisted.")
        referenced.add(allowed_by_lower[name])

    limit = statement.args.get("limit")
    if limit is None:
        statement = statement.limit(max_rows)
    else:
        limit_value = limit.expression
        if (
            not isinstance(limit_value, exp.Literal)
            or not limit_value.is_int
            or int(limit_value.this) > max_rows
        ):
            statement.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))

    return ValidatedQuery(
        sql=statement.sql(dialect="duckdb"),
        views_used=sorted(referenced),
    )


def execute_read_only(database: str | Path, sql: str) -> tuple[list[str], list[tuple]]:
    """Execute validated SQL without write or external-access capabilities."""
    try:
        with duckdb.connect(
            str(database),
            read_only=True,
            config={"enable_external_access": "false"},
        ) as connection:
            cursor = connection.execute(sql)
            columns = [item[0] for item in cursor.description]
            return columns, cursor.fetchall()
    except duckdb.Error as error:
        raise GuardrailError(f"DuckDB could not execute the generated SQL: {error}") from error
