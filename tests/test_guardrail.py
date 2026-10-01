from __future__ import annotations

from decimal import Decimal

import pytest
from sqlglot import exp, parse_one

from app.agent import QueryAnswer, QueryPlan
from app.format import format_answer
from app.guardrail import GuardrailError, validate_sql


ALLOWED = {"v_revenue_lines", "v_account_current", "v_account_metrics"}
MAX_ROWS = 100


def validate(sql: str):
    return validate_sql(sql, ALLOWED, MAX_ROWS)


def result_limit(sql: str) -> int:
    expression = parse_one(sql, read="duckdb")
    return int(expression.args["limit"].expression.this)


def test_valid_select_is_accepted_and_limit_is_injected():
    result = validate("SELECT invoice_id FROM v_revenue_lines")

    assert result.views_used == ["v_revenue_lines"]
    assert result_limit(result.sql) == MAX_ROWS


def test_valid_with_select_is_accepted():
    result = validate(
        """
        WITH totals AS (
            SELECT region, SUM(recognized_revenue_usd) AS revenue
            FROM v_revenue_lines
            GROUP BY region
        )
        SELECT * FROM totals ORDER BY revenue DESC
        """
    )

    assert result.views_used == ["v_revenue_lines"]
    assert result_limit(result.sql) == MAX_ROWS


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("DROP TABLE v_revenue_lines", "Only a read-only SELECT"),
        (
            "SELECT * FROM v_revenue_lines; SELECT * FROM v_account_metrics",
            "Exactly one SQL statement",
        ),
        ("SELECT * FROM invoices", "not allowlisted"),
        ("SELECT * FROM read_csv_auto('data.csv')", "not allowed"),
        ("SELECT * FROM main.v_revenue_lines", "Qualified"),
    ],
)
def test_unsafe_sql_is_rejected(sql, message):
    with pytest.raises(GuardrailError, match=message):
        validate(sql)


def test_limit_below_maximum_is_preserved():
    result = validate("SELECT * FROM v_account_metrics LIMIT 5")

    assert result_limit(result.sql) == 5


def test_limit_above_maximum_is_capped():
    result = validate("SELECT * FROM v_account_metrics LIMIT 1000")

    assert result_limit(result.sql) == MAX_ROWS


def test_nonliteral_limit_is_capped():
    result = validate("SELECT * FROM v_account_metrics LIMIT 5 + 1000")

    assert result_limit(result.sql) == MAX_ROWS


def test_cte_does_not_bypass_allowlist():
    with pytest.raises(GuardrailError, match="not allowlisted"):
        validate("WITH hidden AS (SELECT * FROM raw_invoices) SELECT * FROM hidden")


def test_output_remains_a_select():
    result = validate("SELECT COUNT(*) FROM v_account_current")

    assert isinstance(parse_one(result.sql, read="duckdb"), exp.Select)


def test_scalar_usd_answer_is_formatted_by_application():
    plan = QueryPlan(
        answerable=True,
        clarification_needed=None,
        sql="SELECT SUM(recognized_revenue_usd) AS recognized_revenue_usd",
        views_used=["v_revenue_lines"],
        assumptions=[],
        answer_template="${recognized_revenue_usd}",
    )
    answer = QueryAnswer(
        plan=plan,
        sql=plan.sql,
        columns=["recognized_revenue_usd"],
        rows=[(Decimal("27793767.3715806357"),)],
        views_used=["v_revenue_lines"],
        caveats=[],
    )

    assert format_answer(answer).startswith("Answer\n$27,793,767.37\n")
