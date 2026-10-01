"""Run CloudNova natural-language query evaluations against independent SQL."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import duckdb
import yaml

from app.agent import AgentError, QueryAnswer, answer_question


ROOT = Path(__file__).resolve().parents[1]
TOLERANCE = Decimal("0.01")
REFERENCE_PATTERN = re.compile(
    r"(?ms)^-- (E\d+)\b[^\n]*\n(.*?)(?=^-- E\d+\b|\Z)"
)


@dataclass(frozen=True)
class EvalResult:
    case_id: str
    status: str
    reason: str
    latency_seconds: float


def _as_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _close(actual: Any, expected: Any) -> bool:
    return abs(_as_decimal(actual) - _as_decimal(expected)) <= TOLERANCE


def _reference_queries() -> dict[str, str]:
    text = (ROOT / "evals/reference.sql").read_text(encoding="utf-8")
    return {
        match.group(1): match.group(2).strip()
        for match in REFERENCE_PATTERN.finditer(text)
    }


def _execute_references(cases: list[dict[str, Any]]) -> dict[str, list[tuple]]:
    config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    database = ROOT / config["paths"]["database"]
    queries = _reference_queries()
    needed = {case["reference"] for case in cases if case.get("reference")}
    missing = needed - set(queries)
    if missing:
        raise RuntimeError(f"Missing reference SQL for: {', '.join(sorted(missing))}")

    results: dict[str, list[tuple]] = {}
    with duckdb.connect(
        str(database),
        read_only=True,
        config={"enable_external_access": "false"},
    ) as connection:
        for reference in sorted(needed):
            results[reference] = connection.execute(queries[reference]).fetchall()
    return results


def _column_index(columns: list[str], *terms: str) -> int:
    lowered = [column.lower() for column in columns]
    for term in terms:
        if term in lowered:
            return lowered.index(term)
    for term in terms:
        for index, column in enumerate(lowered):
            if all(part in column for part in term.split("_")):
                return index
    raise ValueError(f"Expected result column matching {terms!r}; got {columns!r}")


def _compare_scalar(answer: QueryAnswer, expected: list[tuple]) -> tuple[bool, str]:
    if len(answer.rows) != 1 or len(answer.rows[0]) != 1:
        return False, "expected one scalar result"
    if not _close(answer.rows[0][0], expected[0][0]):
        return False, f"numeric mismatch: got {answer.rows[0][0]!s}"
    return True, "numeric result matched reference within $0.01"


def _compare_region(answer: QueryAnswer, expected: list[tuple]) -> tuple[bool, str]:
    expected_region, expected_value = expected[0]
    if len(answer.rows) != 1:
        return False, "expected one winning region"
    row = answer.rows[0]
    region_matches = [value for value in row if str(value) == str(expected_region)]
    numeric_matches = [
        value
        for value in row
        if isinstance(value, (int, float, Decimal)) and _close(value, expected_value)
    ]
    if not region_matches or not numeric_matches:
        return False, f"winner/value mismatch: got {row!r}"
    return True, f"{expected_region} and average MRR matched reference"


def _compare_churn(answer: QueryAnswer, expected: list[tuple]) -> tuple[bool, str]:
    expected_rates = {str(row[0]): row[-1] for row in expected}
    try:
        plan_index = _column_index(answer.columns, "plan")
        rate_index = _column_index(answer.columns, "churn_rate", "rate")
        actual_rates = {str(row[plan_index]): row[rate_index] for row in answer.rows}
    except ValueError:
        if len(answer.rows) != 1:
            return False, f"could not identify plan/rate columns in {answer.columns!r}"
        lowered = [column.lower() for column in answer.columns]
        actual_rates = {}
        for plan in expected_rates:
            matches = [
                index
                for index, column in enumerate(lowered)
                if plan.lower() in column and "rate" in column
            ]
            if len(matches) != 1:
                return False, f"could not identify {plan} churn rate column"
            actual_rates[plan] = answer.rows[0][matches[0]]
    if set(actual_rates) != set(expected_rates):
        return False, f"plan mismatch: got {sorted(actual_rates)}"
    mismatches = [
        plan
        for plan, rate in expected_rates.items()
        if not _close(actual_rates[plan], rate)
    ]
    if mismatches:
        return False, f"churn-rate mismatch for {', '.join(mismatches)}"
    return True, "Enterprise and Starter churn rates matched reference"


def _compare_accounts(answer: QueryAnswer, expected: list[tuple]) -> tuple[bool, str]:
    try:
        account_index = _column_index(answer.columns, "account_id")
    except ValueError as error:
        return False, str(error)
    actual_ids = [str(row[account_index]) for row in answer.rows]
    expected_ids = [str(row[0]) for row in expected]
    if actual_ids != expected_ids:
        return False, f"ordered account IDs differed: got {actual_ids!r}"
    return True, "ordered top-5 account IDs matched reference"


def _compare_exposure(answer: QueryAnswer, expected: list[tuple]) -> tuple[bool, str]:
    if len(answer.rows) != 1:
        return False, "expected one exposure result row"
    try:
        count_index = _column_index(
            answer.columns, "exposed_invoice_count", "invoice_count", "count"
        )
        amount_index = _column_index(
            answer.columns, "exposed_usd", "total_exposed_usd"
        )
    except ValueError as error:
        return False, str(error)
    actual = answer.rows[0]
    expected_count, expected_amount = expected[0]
    if int(actual[count_index]) != int(expected_count):
        return False, f"invoice-count mismatch: got {actual[count_index]!s}"
    if not _close(actual[amount_index], expected_amount):
        return False, f"exposed-USD mismatch: got {actual[amount_index]!s}"
    return True, "exposed invoice count and USD matched reference"


COMPARATORS: dict[
    str, Callable[[QueryAnswer, list[tuple]], tuple[bool, str]]
] = {
    "scalar_usd": _compare_scalar,
    "winning_region": _compare_region,
    "churn_rates": _compare_churn,
    "ordered_account_ids": _compare_accounts,
    "exposure": _compare_exposure,
}


def _run_case(
    case: dict[str, Any], references: dict[str, list[tuple]]
) -> EvalResult:
    started = time.perf_counter()
    case_id = case["id"]
    comparison = case["comparison"]
    try:
        answer = answer_question(case["question"], ROOT / "config.yaml")
        if comparison == "expected_failure":
            return EvalResult(
                case_id,
                "XFAIL",
                case["reason"],
                time.perf_counter() - started,
            )
        if comparison == "answerable_false":
            passed = not answer.plan.answerable
            reason = (
                "agent correctly marked the question unanswerable"
                if passed
                else "agent incorrectly marked the unsupported question answerable"
            )
        else:
            if not answer.plan.answerable:
                detail = (
                    answer.plan.clarification_needed
                    or "agent marked the supported question unanswerable"
                )
                return EvalResult(
                    case_id,
                    "FAIL",
                    detail,
                    time.perf_counter() - started,
                )
            passed, reason = COMPARATORS[comparison](
                answer, references[case["reference"]]
            )
        return EvalResult(
            case_id,
            "PASS" if passed else "FAIL",
            reason,
            time.perf_counter() - started,
        )
    except (AgentError, KeyError, TypeError, ValueError) as error:
        if comparison == "expected_failure":
            return EvalResult(
                case_id,
                "XFAIL",
                f"{case['reason']} (observed: {str(error).replace(chr(10), ' ')})",
                time.perf_counter() - started,
            )
        return EvalResult(
            case_id,
            "FAIL",
            str(error).replace("\n", " "),
            time.perf_counter() - started,
        )


def _print_results(results: list[EvalResult]) -> None:
    rows = [
        (result.case_id, result.status, result.reason, f"{result.latency_seconds:.2f}s")
        for result in results
    ]
    headers = ("Case", "Status", "Reason", "Latency")
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    ]
    print(" | ".join(headers[index].ljust(widths[index]) for index in range(4)))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(" | ".join(row[index].ljust(widths[index]) for index in range(4)))


def main() -> int:
    cases_document = yaml.safe_load(
        (ROOT / "evals/cases.yaml").read_text(encoding="utf-8")
    )
    cases = cases_document["cases"]
    try:
        references = _execute_references(cases)
    except (OSError, KeyError, duckdb.Error, RuntimeError) as error:
        print(f"Reference setup failed: {error}")
        return 1

    results = [_run_case(case, references) for case in cases]
    _print_results(results)
    return 1 if any(result.status == "FAIL" for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
