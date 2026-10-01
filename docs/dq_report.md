# CloudNova Data Quality Report

Generated deterministically by `python -m app.pipeline`.

## Row disposition

- Input row count: 5125
- Exact duplicates removed: 82
- Normalization-only duplicates removed: 27
- Conflicts resolved: 16
- Unresolved conflicts quarantined: 0
- Total quarantined rows: 0
- Output invoice count: 5000

## Normalization counts by field

- `billing_cycle`: 3154
- `churned`: 5043
- `payment_method`: 1918
- `plan`: 4208
- `status`: 4142

## Quality checks

- Email repairs: 139
- Invalid emails: 0
- Future-dated invoices: 154
- Invoices before signup: 0
- Amounts reconciled: 5000 of 5000 (100.00%)
