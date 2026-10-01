"""Deterministic terminal formatting for query plans and DuckDB results."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from string import Formatter
from typing import Any

from app.agent import AgentError, QueryAnswer


def _display_value(value: Any, column: str = "") -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        if column.lower().endswith("_usd"):
            return f"${value:,.2f}"
        return format(value, "f")
    if isinstance(value, float):
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    return str(value)


def _render_template(template: str, columns: list[str], row: tuple) -> str:
    available = dict(zip(columns, row))
    for _, field_name, _, conversion in Formatter().parse(template):
        if field_name is None:
            continue
        if field_name not in available or conversion:
            raise AgentError(
                "The answer template contains an invalid result-column placeholder."
            )
    try:
        return template.format_map(available)
    except (KeyError, ValueError) as error:
        raise AgentError(f"The answer template could not be formatted: {error}") from error


def _result_table(columns: list[str], rows: list[tuple]) -> str:
    rendered = [
        [_display_value(value, column) for column, value in zip(columns, row)]
        for row in rows
    ]
    widths = [
        max(len(column), *(len(row[index]) for row in rendered))
        for index, column in enumerate(columns)
    ]
    header = " | ".join(column.ljust(widths[index]) for index, column in enumerate(columns))
    divider = "-+-".join("-" * width for width in widths)
    body = "\n".join(
        " | ".join(value.ljust(widths[index]) for index, value in enumerate(row))
        for row in rendered
    )
    return f"{header}\n{divider}\n{body}"


def format_answer(answer: QueryAnswer) -> str:
    """Format an answer without asking the model to interpret query results."""
    plan = answer.plan
    if not plan.answerable:
        reason = plan.clarification_needed or "This question is not supported by the data."
        assumptions = "\n".join(f"- {item}" for item in plan.assumptions) or "- None"
        return f"Answer\n{reason}\n\nAssumptions\n{assumptions}"

    if (
        len(answer.rows) == 1
        and len(answer.columns) == 1
        and answer.columns[0].lower().endswith("_usd")
    ):
        rendered_answer = _display_value(answer.rows[0][0], answer.columns[0])
    elif plan.answer_template:
        rendered_answer = _render_template(
            plan.answer_template, answer.columns, answer.rows[0]
        )
    elif len(answer.rows) == 1 and len(answer.columns) == 1:
        rendered_answer = _display_value(answer.rows[0][0], answer.columns[0])
    else:
        rendered_answer = "See the deterministic result table below."

    assumptions = "\n".join(f"- {item}" for item in plan.assumptions) or "- None"
    views = "\n".join(f"- {view}" for view in answer.views_used) or "- None"
    caveats = "\n".join(f"- {item}" for item in answer.caveats) or "- None"
    return (
        f"Answer\n{rendered_answer}\n\n"
        f"Result\n{_result_table(answer.columns, answer.rows)}\n\n"
        f"Generated SQL\n{answer.sql}\n\n"
        f"Assumptions\n{assumptions}\n\n"
        f"Rules / views used\n{views}\n\n"
        f"Caveats\n{caveats}"
    )
