# CloudNova Decisions

These decisions are intentionally explicit because the source data and customer brief contain ambiguities. Implementation follows these decisions unless a later commit records a deliberate change.

## D1 — Track B: deterministic data model + NL-to-SQL

Use a cleaning/modeling pipeline and constrained natural-language-to-SQL layer.

Why: revenue, MRR, churn, refunds, and exposure require exact aggregation. A vector-retrieval layer would not make those calculations more trustworthy.

## D2 — FX conversion uses division for this prototype

Use:

`amount_usd = amount_local / fx_rate`

with USD=1.00, EUR=1.08, GBP=1.27.

Why: the supplied invoice amounts reconcile to list price × seats × billed months × discount only under division. Treat this as a data-contract inconsistency to confirm with Finance, not as a universal FX convention.

Rejected: literal multiplication from the wording of the brief, because it does not reconcile to the generated invoice amounts.

## D3 — Three-pass invoice deduplication

1. Drop exact duplicate rows.
2. Drop same-invoice rows that become identical after normalization.
3. Resolve remaining status conflicts using:

`refunded > void > failed > pending > paid`

Why: the export has no source-system authority or update timestamp. A deterministic conservative rule is safer for a finance prototype than optimistic revenue recognition.

All step-3 conflicts must be logged.

## D4 — Deterministic date parsing

- YYYY-MM-DD: ISO
- YYYY/MM/DD: year-first
- Mon D YYYY: English month
- NN/NN/YYYY: month-first
- NN-NN-YYYY: day-first

Why: this matches the examples in the customer brief and avoids silent parser guessing.

## D5 — Account grain uses account_id with a caveat

Use `account_id` because the supplied data dictionary defines it as the account key.

Current attributes come from the latest invoice by invoice_date, then invoice_id as tie-breaker.

Caveat: profiling indicates account_id is not a reliable real-world customer identifier. Account-level outputs must surface this caveat.

Rejected: account_name as a replacement key because it would merge records based on a non-key display field.

## D6 — Recognized revenue is net

Recognized revenue is the sum of `amount_usd` for canonical statuses `paid` and `refunded`.

Refunded amounts are negative and therefore net against revenue.

Pending, failed, and void invoices do not count.

## D7 — MRR comes from subscription terms, not invoice amount

`MRR = list_price(plan) * seats * (1 - discount_pct / 100)`

For account-level current MRR, use the latest invoice terms and include MRR only when the account is not churned.

Annual invoices still represent the same monthly MRR.

## D8 — Churn rate is account-level current state

Churn rate by plan is:

`churned accounts / accounts`

using the latest invoice's plan and churn flag for each account_id.

## D9 — Quarantine hard failures

Unparseable required values, unknown required categories, amount/currency suffix mismatches, and unresolved duplicate conflicts go to quarantine with a reason.

Never silently discard them.

## D10 — Keep and flag date anomalies

Future-dated invoices and invoices before signup remain in the clean dataset but receive quality flags and counts in the DQ report.

## D11 — Repair only the obvious email defect

Replace `_at_` with `@` only when the result validates as a basic email address and mark `email_repaired=true`.

Other invalid nonblank emails become null with an invalid flag. Email issues do not quarantine the row.

## D12 — Production hardening item is the SQL guardrail

Generated SQL is parsed before execution, restricted to one read-only SELECT over allowlisted semantic views, capped by row limit, and executed through read-only DuckDB with external access disabled.

Why: arbitrary SQL execution is the principal production risk introduced by the natural-language query layer.
