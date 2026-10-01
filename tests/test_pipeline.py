from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pandas as pd
import pytest
import yaml

from app.pipeline import (
    deduplicate_normalized,
    load_config,
    normalize_row,
    parse_date,
    read_source,
    run_pipeline,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config():
    return load_config(ROOT / "config.yaml")[0]


def source_row(**overrides: str) -> dict[str, str]:
    row = {
        "invoice_id": "INV-1",
        "account_id": "ACC-1",
        "account_name": "Acme",
        "contact_email": "billing_at_acme.com",
        "region": "NA",
        "industry": "",
        "plan": "Tier 1",
        "billing_cycle": "Monthly",
        "seats": "10",
        "currency": "EUR",
        "amount": "529.20",
        "discount_pct": "0",
        "status": "Complete",
        "payment_method": "ACH",
        "signup_date": "2023-01-01",
        "invoice_date": "2024/01/05",
        "churned": "N",
        "csat_score": "",
        "support_tickets": "0",
    }
    row.update(overrides)
    return row


def test_source_load_preserves_na_and_uses_source_columns_for_exact_duplicates(tmp_path):
    rows = [source_row(), source_row()]
    path = tmp_path / "source.csv"
    pd.DataFrame(rows).to_csv(path, index=False)

    loaded = read_source(path)

    assert loaded.loc[0, "region"] == "NA"
    assert loaded["source_row"].tolist() == [1, 2]
    source_columns = [column for column in loaded.columns if column != "source_row"]
    duplicate_mask = loaded.duplicated(subset=source_columns, keep="first")
    assert duplicate_mask.tolist() == [False, True]


def test_normalization_fx_reconciliation_and_email_repair(config):
    row, failure, counts = normalize_row(source_row(amount="€529.20"), 1, config)

    assert failure is None
    assert row["region"] == "NA"
    assert row["plan"] == "Starter"
    assert row["status"] == "paid"
    assert row["amount_usd"] == row["expected_billed_usd"]
    assert row["amount_reconciles"] is True
    assert row["contact_email"] == "billing@acme.com"
    assert row["email_repaired"] is True
    assert counts["status"] == 1


@pytest.mark.parametrize(
    ("raw", "expected", "branch"),
    [
        ("2024-01-05", date(2024, 1, 5), "YYYY-MM-DD"),
        ("2024/01/05", date(2024, 1, 5), "YYYY/MM/DD"),
        ("Jan 5 2024", date(2024, 1, 5), "Mon D YYYY"),
        ("01/05/2024", date(2024, 1, 5), "NN/NN/YYYY"),
        ("05-01-2024", date(2024, 1, 5), "NN-NN-YYYY"),
    ],
)
def test_date_parsing_branches(raw, expected, branch):
    assert parse_date(raw) == (expected, branch)


def test_status_precedence_and_unresolved_same_status(config):
    paid, failure, _ = normalize_row(source_row(status="paid"), 1, config)
    assert failure is None
    refunded, failure, _ = normalize_row(
        source_row(status="refunded", amount="-529.20"), 2, config
    )
    assert failure is None

    invoices, removed, conflicts, unresolved, unresolved_count = deduplicate_normalized(
        [paid, refunded], config
    )
    assert removed == 0
    assert invoices[0]["status"] == "refunded"
    assert conflicts[0]["winning_source_row"] == 2
    assert not unresolved
    assert unresolved_count == 0

    second_refund = deepcopy(refunded)
    second_refund["source_row"] = 3
    second_refund["amount_local"] -= 1
    (
        invoices,
        _,
        conflicts,
        unresolved,
        unresolved_count,
    ) = deduplicate_normalized([refunded, second_refund], config)
    assert not invoices
    assert not conflicts
    assert len(unresolved) == 2
    assert unresolved_count == 1


def test_complete_pipeline_dedup_and_views(tmp_path, config):
    rows = [
        source_row(invoice_id="INV-A", currency="USD", amount="490"),
        source_row(invoice_id="INV-A", currency="USD", amount="490"),
        source_row(invoice_id="INV-B", currency="USD", amount="490.00", status="paid"),
        source_row(invoice_id="INV-B", currency="USD", amount="490.00", status="Paid"),
        source_row(invoice_id="INV-C", currency="USD", amount="490.00", status="paid"),
        source_row(
            invoice_id="INV-C", currency="USD", amount="-490.00", status="refunded"
        ),
    ]
    raw_path = tmp_path / "source.csv"
    database_path = tmp_path / "test.duckdb"
    report_path = tmp_path / "dq.md"
    pd.DataFrame(rows).to_csv(raw_path, index=False)

    test_config = deepcopy(config)
    test_config["paths"] = {
        "raw_csv": str(raw_path),
        "database": str(database_path),
        "dq_report": str(report_path),
        "views_sql": str(ROOT / "models/views.sql"),
        "catalog": str(ROOT / "models/catalog.yaml"),
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(test_config), encoding="utf-8")

    metrics = run_pipeline(config_path)

    assert metrics["input_row_count"] == 6
    assert metrics["exact_duplicates_removed"] == 1
    assert metrics["normalization_only_duplicates_removed"] == 1
    assert metrics["conflicts_resolved"] == 1
    assert metrics["output_invoice_count"] == 3
    with duckdb.connect(str(database_path), read_only=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM raw_invoices").fetchone()[0] == 6
        assert connection.execute("SELECT COUNT(*) FROM v_revenue_lines").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM v_account_current").fetchone()[0] == 1
        amount = connection.execute(
            "SELECT amount_usd FROM invoices WHERE invoice_id = 'INV-A'"
        ).fetchone()[0]
        assert amount == Decimal("490")
