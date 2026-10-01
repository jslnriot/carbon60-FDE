"""Deterministic cleaning and modeling pipeline for the CloudNova invoice ledger."""

from __future__ import annotations

import argparse
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import yaml


CANONICAL_COLUMNS = [
    "source_row",
    "invoice_id",
    "account_id",
    "account_name",
    "contact_email",
    "email_repaired",
    "email_invalid",
    "region",
    "industry",
    "plan",
    "billing_cycle",
    "seats",
    "currency",
    "amount_local",
    "fx_rate",
    "amount_usd",
    "discount_pct",
    "status",
    "payment_method",
    "signup_date",
    "invoice_date",
    "date_format_detected",
    "churned",
    "csat_score",
    "support_tickets",
    "billed_months",
    "expected_billed_usd",
    "amount_reconciles",
    "subscription_mrr_usd",
    "is_future_dated",
    "invoice_before_signup",
]

BUSINESS_DEDUP_COLUMNS = [column for column in CANONICAL_COLUMNS if column != "source_row"]
INTEGER_RE = re.compile(r"^[+-]?\d+$")
DECIMAL_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
AMOUNT_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
TRAILING_CURRENCY_RE = re.compile(r"\s+([A-Za-z]{3})\s*$")


def load_config(config_path: str | Path = "config.yaml") -> tuple[dict[str, Any], Path]:
    """Load configuration and return it with its repository-relative base path."""
    path = Path(config_path).resolve()
    with path.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    return config, path.parent


def read_source(path: str | Path) -> pd.DataFrame:
    """Read every source field as a string and add one-based audit metadata."""
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    frame.insert(0, "source_row", range(1, len(frame) + 1))
    return frame


def _normalization_maps(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    maps: dict[str, dict[str, Any]] = {}
    for field, canonical_values in config["normalization"].items():
        lookup: dict[str, Any] = {}
        for canonical, variants in canonical_values.items():
            value: Any = canonical
            if field == "churned":
                value = str(canonical).lower() == "true"
            for variant in variants:
                lookup[str(variant).strip()] = value
        maps[field] = lookup
    return maps


def _parse_category(
    raw: str,
    field: str,
    maps: dict[str, dict[str, Any]],
    *,
    nullable: bool = False,
) -> Any:
    value = raw.strip()
    if not value and nullable:
        return None
    if value not in maps[field]:
        raise ValueError(f"unmapped {field}: {raw!r}")
    return maps[field][value]


def _parse_integer(raw: str, field: str, minimum: int, maximum: int | None = None) -> int:
    value = raw.strip()
    if not INTEGER_RE.fullmatch(value):
        raise ValueError(f"{field} is not an integer: {raw!r}")
    parsed = int(value)
    if parsed < minimum or (maximum is not None and parsed > maximum):
        bounds = f"{minimum}..{maximum}" if maximum is not None else f">={minimum}"
        raise ValueError(f"{field} must be {bounds}: {raw!r}")
    return parsed


def _parse_decimal(raw: str, field: str) -> Decimal:
    value = raw.strip()
    if not DECIMAL_RE.fullmatch(value):
        raise ValueError(f"{field} is not numeric: {raw!r}")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"{field} is not numeric: {raw!r}") from error
    if not parsed.is_finite():
        raise ValueError(f"{field} is not finite: {raw!r}")
    return parsed


def parse_amount(raw: str, currency: str) -> Decimal:
    """Parse only the amount formats permitted by the data-model contract."""
    value = raw.strip()
    suffix = TRAILING_CURRENCY_RE.search(value)
    if suffix:
        suffix_currency = suffix.group(1).upper()
        if suffix_currency != currency:
            raise ValueError(
                f"amount currency suffix {suffix_currency!r} does not match {currency!r}"
            )
        value = value[: suffix.start()].strip()
    value = (
        value.replace(",", "")
        .replace("$", "")
        .replace("£", "")
        .replace("€", "")
        .strip()
    )
    if not AMOUNT_RE.fullmatch(value):
        raise ValueError(f"unsupported amount format: {raw!r}")
    parsed = Decimal(value)
    if not parsed.is_finite():
        raise ValueError(f"amount is not finite: {raw!r}")
    return parsed


DATE_FORMATS = [
    (re.compile(r"^\d{4}-\d{2}-\d{2}$"), "%Y-%m-%d", "YYYY-MM-DD"),
    (re.compile(r"^\d{4}/\d{2}/\d{2}$"), "%Y/%m/%d", "YYYY/MM/DD"),
    (re.compile(r"^[A-Za-z]{3} \d{1,2} \d{4}$"), "%b %d %Y", "Mon D YYYY"),
    (re.compile(r"^\d{2}/\d{2}/\d{4}$"), "%m/%d/%Y", "NN/NN/YYYY"),
    (re.compile(r"^\d{2}-\d{2}-\d{4}$"), "%d-%m-%Y", "NN-NN-YYYY"),
]


def parse_date(raw: str) -> tuple[date, str]:
    value = raw.strip()
    for pattern, parser_format, branch in DATE_FORMATS:
        if pattern.fullmatch(value):
            try:
                return datetime.strptime(value, parser_format).date(), branch
            except ValueError as error:
                raise ValueError(f"invalid date value: {raw!r}") from error
    raise ValueError(f"unsupported date format: {raw!r}")


def parse_email(raw: str) -> tuple[str | None, bool, bool]:
    value = raw.strip()
    if not value:
        return None, False, False
    if "_at_" in value:
        repaired = value.replace("_at_", "@")
        if EMAIL_RE.fullmatch(repaired):
            return repaired, True, False
    if EMAIL_RE.fullmatch(value):
        return value, False, False
    return None, False, True


def normalize_row(
    source: dict[str, str],
    source_row: int,
    config: dict[str, Any],
    maps: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any] | None, tuple[str, str] | None, Counter[str]]:
    """Normalize one row, returning a canonical row or one hard-failure reason."""
    maps = maps or _normalization_maps(config)
    normalization_counts: Counter[str] = Counter()

    try:
        required_strings = {}
        for field in ("invoice_id", "account_id", "account_name"):
            required_strings[field] = source[field].strip()
            if not required_strings[field]:
                raise ValueError(f"{field} is blank")

        region = source["region"].strip()
        if region not in config["allowed_values"]["region"]:
            raise ValueError(f"unmapped region: {source['region']!r}")
        currency = source["currency"].strip()
        if currency not in config["allowed_values"]["currency"]:
            raise ValueError(f"unmapped currency: {source['currency']!r}")

        normalized: dict[str, Any] = {}
        for field in ("plan", "billing_cycle", "status", "payment_method", "churned"):
            normalized[field] = _parse_category(
                source[field], field, maps, nullable=field == "payment_method"
            )
            if normalized[field] is not None and source[field] != str(normalized[field]):
                normalization_counts[field] += 1

        seats = _parse_integer(source["seats"], "seats", 1)
        discount_pct = _parse_decimal(source["discount_pct"], "discount_pct")
        if not Decimal("0") <= discount_pct <= Decimal("100"):
            raise ValueError(f"discount_pct must be 0..100: {source['discount_pct']!r}")
        support_tickets = _parse_integer(source["support_tickets"], "support_tickets", 0)
        csat_score = None
        if source["csat_score"].strip():
            csat_score = _parse_integer(source["csat_score"], "csat_score", 1, 5)

        amount_local = parse_amount(source["amount"], currency)
        signup_date, _ = parse_date(source["signup_date"])
        invoice_date, invoice_date_branch = parse_date(source["invoice_date"])
        contact_email, email_repaired, email_invalid = parse_email(source["contact_email"])

        fx_rate = Decimal(str(config["fx"]["rates"][currency]))
        if config["fx"]["mode"] != "divide":
            raise ValueError(f"unsupported configured FX mode: {config['fx']['mode']!r}")
        amount_usd = amount_local / fx_rate
        billed_months = 1 if normalized["billing_cycle"] == "monthly" else 10
        price = Decimal(str(config["pricing_usd_per_seat_month"][normalized["plan"]]))
        discount_factor = Decimal("1") - discount_pct / Decimal("100")
        expected_billed_usd = price * seats * billed_months * discount_factor
        subscription_mrr_usd = price * seats * discount_factor
        tolerance = Decimal(str(config["quality"]["amount_reconciliation_tolerance_usd"]))
        amount_reconciles = abs(abs(amount_usd) - expected_billed_usd) <= tolerance
        as_of_date = date.fromisoformat(str(config["quality"]["as_of_date"]))

        row = {
            "source_row": source_row,
            **required_strings,
            "contact_email": contact_email,
            "email_repaired": email_repaired,
            "email_invalid": email_invalid,
            "region": region,
            "industry": source["industry"].strip() or None,
            "plan": normalized["plan"],
            "billing_cycle": normalized["billing_cycle"],
            "seats": seats,
            "currency": currency,
            "amount_local": amount_local,
            "fx_rate": fx_rate,
            "amount_usd": amount_usd,
            "discount_pct": discount_pct,
            "status": normalized["status"],
            "payment_method": normalized["payment_method"],
            "signup_date": signup_date,
            "invoice_date": invoice_date,
            "date_format_detected": invoice_date_branch,
            "churned": normalized["churned"],
            "csat_score": csat_score,
            "support_tickets": support_tickets,
            "billed_months": billed_months,
            "expected_billed_usd": expected_billed_usd,
            "amount_reconciles": amount_reconciles,
            "subscription_mrr_usd": subscription_mrr_usd,
            "is_future_dated": invoice_date > as_of_date,
            "invoice_before_signup": invoice_date < signup_date,
        }
        return row, None, normalization_counts
    except (KeyError, TypeError, ValueError, InvalidOperation, ZeroDivisionError) as error:
        return None, ("invalid_required_value", str(error)), Counter()


def _dedup_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row[column] for column in BUSINESS_DEDUP_COLUMNS)


def deduplicate_normalized(
    rows: list[dict[str, Any]], config: dict[str, Any]
) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]], list[dict[str, Any]], int]:
    """Apply normalization-only and configured status-precedence deduplication."""
    normalization_duplicates = 0
    stage_two: list[dict[str, Any]] = []
    seen_by_invoice: dict[str, set[tuple[Any, ...]]] = defaultdict(set)
    for row in sorted(rows, key=lambda item: item["source_row"]):
        key = _dedup_key(row)
        if key in seen_by_invoice[row["invoice_id"]]:
            normalization_duplicates += 1
            continue
        seen_by_invoice[row["invoice_id"]].add(key)
        stage_two.append(row)

    precedence = {
        status: rank for rank, status in enumerate(config["dedup"]["status_precedence"])
    }
    by_invoice: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in stage_two:
        by_invoice[row["invoice_id"]].append(row)

    invoices: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    unresolved_invoice_count = 0
    for invoice_id, group in by_invoice.items():
        group.sort(key=lambda item: item["source_row"])
        if len(group) == 1:
            invoices.append(group[0])
            continue
        winning_status = min((row["status"] for row in group), key=precedence.__getitem__)
        winners = [row for row in group if row["status"] == winning_status]
        if len(winners) != 1:
            unresolved_invoice_count += 1
            for row in group:
                unresolved.append(
                    {
                        "source_row": row["source_row"],
                        "invoice_id": invoice_id,
                        "reason_code": "unresolved_duplicate_conflict",
                        "reason_detail": (
                            f"multiple materially different rows share winning status "
                            f"{winning_status!r}"
                        ),
                    }
                )
            continue
        winner = winners[0]
        invoices.append(winner)
        conflicts.append(
            {
                "invoice_id": invoice_id,
                "source_rows": ",".join(str(row["source_row"]) for row in group),
                "statuses_seen": ",".join(dict.fromkeys(row["status"] for row in group)),
                "winning_status": winning_status,
                "winning_source_row": winner["source_row"],
                "rule": "configured_status_precedence",
            }
        )

    invoices.sort(key=lambda item: item["source_row"])
    return (
        invoices,
        normalization_duplicates,
        conflicts,
        unresolved,
        unresolved_invoice_count,
    )


def _write_database(
    database_path: Path,
    raw: pd.DataFrame,
    invoices: list[dict[str, Any]],
    quarantine: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    views_sql_path: Path,
) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(database_path)) as connection:
        connection.execute("DROP VIEW IF EXISTS v_account_metrics")
        connection.execute("DROP VIEW IF EXISTS v_account_current")
        connection.execute("DROP VIEW IF EXISTS v_revenue_lines")
        for table in ("raw_invoices", "invoices", "quarantine", "dq_conflicts"):
            connection.execute(f"DROP TABLE IF EXISTS {table}")

        connection.register("raw_frame", raw)
        connection.execute("CREATE TABLE raw_invoices AS SELECT * FROM raw_frame")
        connection.unregister("raw_frame")

        invoice_frame = pd.DataFrame(invoices, columns=CANONICAL_COLUMNS)
        for column in (
            "amount_local",
            "fx_rate",
            "amount_usd",
            "discount_pct",
            "expected_billed_usd",
            "subscription_mrr_usd",
        ):
            invoice_frame[column] = invoice_frame[column].map(lambda value: format(value, "f"))
        connection.register("invoice_frame", invoice_frame)
        connection.execute(
            """
            CREATE TABLE invoices (
                source_row INTEGER NOT NULL,
                invoice_id VARCHAR NOT NULL,
                account_id VARCHAR NOT NULL,
                account_name VARCHAR NOT NULL,
                contact_email VARCHAR,
                email_repaired BOOLEAN NOT NULL,
                email_invalid BOOLEAN NOT NULL,
                region VARCHAR NOT NULL,
                industry VARCHAR,
                plan VARCHAR NOT NULL,
                billing_cycle VARCHAR NOT NULL,
                seats INTEGER NOT NULL,
                currency VARCHAR NOT NULL,
                amount_local DECIMAL(38, 10) NOT NULL,
                fx_rate DECIMAL(38, 10) NOT NULL,
                amount_usd DECIMAL(38, 10) NOT NULL,
                discount_pct DECIMAL(38, 10) NOT NULL,
                status VARCHAR NOT NULL,
                payment_method VARCHAR,
                signup_date DATE NOT NULL,
                invoice_date DATE NOT NULL,
                date_format_detected VARCHAR NOT NULL,
                churned BOOLEAN NOT NULL,
                csat_score INTEGER,
                support_tickets INTEGER NOT NULL,
                billed_months INTEGER NOT NULL,
                expected_billed_usd DECIMAL(38, 10) NOT NULL,
                amount_reconciles BOOLEAN NOT NULL,
                subscription_mrr_usd DECIMAL(38, 10) NOT NULL,
                is_future_dated BOOLEAN NOT NULL,
                invoice_before_signup BOOLEAN NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO invoices
            SELECT
                source_row, invoice_id, account_id, account_name, contact_email,
                email_repaired, email_invalid, region, industry, plan, billing_cycle,
                seats, currency, amount_local, fx_rate, amount_usd, discount_pct,
                status, payment_method, signup_date, invoice_date,
                date_format_detected, churned, csat_score, support_tickets,
                billed_months, expected_billed_usd, amount_reconciles,
                subscription_mrr_usd, is_future_dated, invoice_before_signup
            FROM invoice_frame
            """
        )
        connection.unregister("invoice_frame")

        quarantine_frame = pd.DataFrame(
            quarantine, columns=["source_row", "invoice_id", "reason_code", "reason_detail"]
        )
        connection.register("quarantine_frame", quarantine_frame)
        connection.execute(
            """
            CREATE TABLE quarantine (
                source_row INTEGER NOT NULL,
                invoice_id VARCHAR,
                reason_code VARCHAR NOT NULL,
                reason_detail VARCHAR NOT NULL
            )
            """
        )
        connection.execute("INSERT INTO quarantine SELECT * FROM quarantine_frame")
        connection.unregister("quarantine_frame")

        conflict_frame = pd.DataFrame(
            conflicts,
            columns=[
                "invoice_id",
                "source_rows",
                "statuses_seen",
                "winning_status",
                "winning_source_row",
                "rule",
            ],
        )
        connection.register("conflict_frame", conflict_frame)
        connection.execute(
            """
            CREATE TABLE dq_conflicts (
                invoice_id VARCHAR NOT NULL,
                source_rows VARCHAR NOT NULL,
                statuses_seen VARCHAR NOT NULL,
                winning_status VARCHAR NOT NULL,
                winning_source_row INTEGER NOT NULL,
                rule VARCHAR NOT NULL
            )
            """
        )
        connection.execute("INSERT INTO dq_conflicts SELECT * FROM conflict_frame")
        connection.unregister("conflict_frame")
        connection.execute(views_sql_path.read_text(encoding="utf-8"))


def _render_report(metrics: dict[str, Any], path: Path) -> None:
    normalization_lines = "\n".join(
        f"- `{field}`: {count}"
        for field, count in sorted(metrics["normalization_counts"].items())
    )
    percentage = metrics["amount_reconciliation_percentage"]
    report = f"""# CloudNova Data Quality Report

Generated deterministically by `python -m app.pipeline`.

## Row disposition

- Input row count: {metrics["input_row_count"]}
- Exact duplicates removed: {metrics["exact_duplicates_removed"]}
- Normalization-only duplicates removed: {metrics["normalization_only_duplicates_removed"]}
- Conflicts resolved: {metrics["conflicts_resolved"]}
- Unresolved conflicts quarantined: {metrics["unresolved_conflicts_quarantined"]}
- Total quarantined rows: {metrics["total_quarantined_rows"]}
- Output invoice count: {metrics["output_invoice_count"]}

## Normalization counts by field

{normalization_lines or "- None"}

## Quality checks

- Email repairs: {metrics["email_repairs"]}
- Invalid emails: {metrics["invalid_emails"]}
- Future-dated invoices: {metrics["future_dated_count"]}
- Invoices before signup: {metrics["invoice_before_signup_count"]}
- Amounts reconciled: {metrics["amount_reconciles_count"]} of {metrics["output_invoice_count"]} ({percentage:.2f}%)
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")


def run_pipeline(config_path: str | Path = "config.yaml") -> dict[str, Any]:
    """Run the complete deterministic pipeline and return its DQ metrics."""
    config, base = load_config(config_path)
    paths = {name: base / value for name, value in config["paths"].items()}
    raw = read_source(paths["raw_csv"])
    source_columns = [column for column in raw.columns if column != "source_row"]

    # Stage 1 deliberately excludes source_row from equality and keeps the earliest row.
    exact_duplicate_mask = raw.duplicated(subset=source_columns, keep="first")
    exact_duplicates_removed = int(exact_duplicate_mask.sum())
    stage_one = raw.loc[~exact_duplicate_mask]

    maps = _normalization_maps(config)
    normalized_rows: list[dict[str, Any]] = []
    quarantine: list[dict[str, Any]] = []
    normalization_counts: Counter[str] = Counter()
    for record in stage_one.to_dict(orient="records"):
        source_row = int(record.pop("source_row"))
        canonical, failure, row_counts = normalize_row(record, source_row, config, maps)
        normalization_counts.update(row_counts)
        if failure:
            quarantine.append(
                {
                    "source_row": source_row,
                    "invoice_id": record.get("invoice_id", "").strip() or None,
                    "reason_code": failure[0],
                    "reason_detail": failure[1],
                }
            )
        else:
            normalized_rows.append(canonical)

    (
        invoices,
        normalization_duplicates_removed,
        conflicts,
        unresolved,
        unresolved_invoice_count,
    ) = deduplicate_normalized(normalized_rows, config)
    quarantine.extend(unresolved)

    output_count = len(invoices)
    amount_reconciles_count = sum(row["amount_reconciles"] for row in invoices)
    metrics = {
        "input_row_count": len(raw),
        "exact_duplicates_removed": exact_duplicates_removed,
        "normalization_only_duplicates_removed": normalization_duplicates_removed,
        "conflicts_resolved": len(conflicts),
        "unresolved_conflicts_quarantined": unresolved_invoice_count,
        "total_quarantined_rows": len(quarantine),
        "output_invoice_count": output_count,
        "normalization_counts": dict(normalization_counts),
        "email_repairs": sum(row["email_repaired"] for row in invoices),
        "invalid_emails": sum(row["email_invalid"] for row in invoices),
        "future_dated_count": sum(row["is_future_dated"] for row in invoices),
        "invoice_before_signup_count": sum(row["invoice_before_signup"] for row in invoices),
        "amount_reconciles_count": amount_reconciles_count,
        "amount_reconciliation_percentage": (
            amount_reconciles_count / output_count * 100 if output_count else 0.0
        ),
    }
    _write_database(
        paths["database"],
        raw,
        invoices,
        quarantine,
        conflicts,
        paths["views_sql"],
    )
    _render_report(metrics, paths["dq_report"])
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()
    metrics = run_pipeline(args.config)
    for key, value in metrics.items():
        if key != "normalization_counts":
            print(f"{key}: {value}")


if __name__ == "__main__":
    main()
