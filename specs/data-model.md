# CloudNova Data Model Spec

Status: implementation contract  
Source: `data/cloudnova_invoices.csv` + `data/BUSINESS_CONTEXT.md`

## 1. Source load

- Read the CSV with `dtype=str, keep_default_na=False`.
- Preserve every source column.
- Add `source_row`, starting at 1 for the first data row.
- Never mutate the raw CSV.
- The pipeline writes a DuckDB database at the configured path.

## 2. Canonical invoice schema

The cleaned `invoices` table contains one row per resolved `invoice_id`.

Required canonical fields:

| Field | Type | Nullable | Rule |
|---|---|---:|---|
| source_row | integer | no | original data-row number |
| invoice_id | string | no | trimmed |
| account_id | string | no | trimmed |
| account_name | string | no | trimmed |
| contact_email | string | yes | cleaned per §5 |
| email_repaired | boolean | no | true only when `_at_` repair succeeds |
| email_invalid | boolean | no | true when nonblank email cannot be validated |
| region | string | no | NA / EMEA / APAC / LATAM |
| industry | string | yes | blank -> null |
| plan | string | no | Starter / Pro / Enterprise |
| billing_cycle | string | no | monthly / annual |
| seats | integer | no | > 0 |
| currency | string | no | USD / EUR / GBP |
| amount_local | decimal | no | parsed signed invoice amount |
| fx_rate | decimal | no | configured rate |
| amount_usd | decimal | no | §6 |
| discount_pct | decimal | no | 0..100 |
| status | string | no | paid / pending / failed / refunded / void |
| payment_method | string | yes | credit_card / ach / wire / invoice |
| signup_date | date | no | §5 |
| invoice_date | date | no | §5 |
| date_format_detected | string | no | parser branch used for invoice_date |
| churned | boolean | no | normalized |
| csat_score | integer | yes | 1..5 or null |
| support_tickets | integer | no | >= 0 |
| billed_months | integer | no | monthly=1, annual=10 |
| expected_billed_usd | decimal | no | §6 |
| amount_reconciles | boolean | no | §6 |
| subscription_mrr_usd | decimal | no | §7 |
| is_future_dated | boolean | no | §8 |
| invoice_before_signup | boolean | no | §8 |

## 3. Category normalization

Mappings come from `config.yaml`.

### Plan

- Starter: Starter, starter, STARTER, Start, Tier 1
- Pro: Pro, pro, PRO, Professional, Tier 2
- Enterprise: Enterprise, enterprise, ENT, Tier 3

### Billing cycle

- monthly: monthly, Monthly
- annual: annual, Annual, annually, yearly

### Status

- paid: paid, Paid, PAID, Complete, completed, success
- pending: pending, Pending, PENDING, in_review, awaiting
- failed: failed, Failed, FAILED, declined, error
- refunded: refunded, Refunded, REFUND, charged_back
- void: void, Void, VOID, cancelled, canceled

### Payment method

- credit_card: credit_card, Credit Card
- ach: ach, ACH
- wire: wire, Wire Transfer
- invoice: invoice
- blank is allowed and becomes null

### Churned

Normalize true/TRUE/Yes/1/Y to true and false/FALSE/No/0/N to false.

Any nonblank, unmapped required category is a hard failure and goes to quarantine.

## 4. Numeric validation

- `seats`: integer > 0.
- `discount_pct`: numeric from 0 through 100.
- `csat_score`: blank -> null; otherwise integer 1 through 5.
- `support_tickets`: integer >= 0.
- Failed required numeric parsing goes to quarantine.

## 5. String, amount, date, and email parsing

### Amount

1. Trim whitespace.
2. Accept plain numeric strings or strings containing `$`, `£`, `€`, commas, or a trailing currency code.
3. If a trailing currency code exists, it must match the row's `currency`; mismatch -> quarantine.
4. Strip formatting and parse to signed decimal.
5. Preserve negative refunds.

### Dates

Parse using these deterministic rules, in this order:

1. `YYYY-MM-DD` -> ISO.
2. `YYYY/MM/DD` -> year-first.
3. `Mon D YYYY` -> English abbreviated month.
4. `NN/NN/YYYY` -> month-first.
5. `NN-NN-YYYY` -> day-first.

Do not guess another format. Failure -> quarantine.

Store the parser branch used in `date_format_detected` for `invoice_date`.

### Email

- Blank -> null.
- If `_at_` occurs and replacing it with `@` produces a valid basic email, keep the repaired email and set `email_repaired=true`.
- Otherwise validate with a simple address-shape check.
- A nonblank invalid email becomes null with `email_invalid=true`; it is not a quarantine reason because email is nullable.
- Never impute an email.

## 6. FX and invoice reconciliation

Configured rates:

- USD 1.00
- EUR 1.08
- GBP 1.27

Locked prototype rule:

`amount_usd = amount_local / fx_rate`

This deliberately follows the data reconciliation finding documented in `docs/decisions.md`, not the literal wording of the FX sentence in the brief.

Derived values:

- `billed_months = 1` for monthly, `10` for annual.
- `expected_billed_usd = list_price_usd(plan) * seats * billed_months * (1 - discount_pct / 100)`.
- `amount_reconciles = abs(abs(amount_usd) - expected_billed_usd) <= configured tolerance`.

Refund signs do not cause reconciliation failure; compare the absolute invoice amount to expected billed value.

## 7. MRR

For every canonical invoice row:

`subscription_mrr_usd = list_price_usd(plan) * seats * (1 - discount_pct / 100)`

Annual billing still uses one month of underlying MRR. Do not divide or multiply MRR by the annual invoice amount.

## 8. Data-quality flags

Keep these rows; do not quarantine them:

- `is_future_dated = invoice_date > quality.as_of_date`
- `invoice_before_signup = invoice_date < signup_date`

The DQ report must count both.

## 9. Deduplication and quarantine

Deduplication is performed in this order:

1. Remove exact duplicate source rows, retaining one representative source row.
2. Normalize and parse, then remove rows for the same `invoice_id` that are identical across canonical business fields after normalization.
3. For remaining `invoice_id` conflicts, choose the row whose canonical status has highest configured precedence:

`refunded > void > failed > pending > paid`

Every step-3 resolution is written to `dq_conflicts` with at least:

- invoice_id
- source_rows
- statuses_seen
- winning_status
- winning_source_row
- rule

If multiple materially different rows remain for the same invoice_id at the same winning status, do not invent a tie-breaker: quarantine that invoice_id as `unresolved_duplicate_conflict`.

Hard failures are never silently discarded. Write them to `quarantine` with:

- source_row
- invoice_id if available
- reason_code
- reason_detail

The pipeline generates `docs/dq_report.md` with:

- input row count
- exact duplicates removed
- normalization-only duplicates removed
- conflicts resolved
- unresolved conflicts quarantined
- total quarantined rows
- output invoice count
- normalization counts by field
- email repairs / invalid emails
- future-dated count
- invoice-before-signup count
- amount reconciliation count and percentage

## 10. Semantic views

### `v_revenue_lines`

Grain: one row per clean invoice.

Expose enough fields for invoice-level financial questions, including:

- invoice_id, account_id, account_name
- region, plan, status
- invoice_date, invoice_year, invoice_month
- amount_usd
- `is_recognized`: status in (`paid`, `refunded`)
- `recognized_revenue_usd`: amount_usd for paid/refunded, else 0
- `is_refund`: status = refunded
- `refund_usd`: absolute amount_usd for refunded, else 0
- `is_exposed`: status in (`pending`, `failed`)
- `exposed_usd`: absolute amount_usd for pending/failed, else 0

Recognized revenue nets refunded rows because refund amounts are negative.

### `v_account_current`

Grain: one row per `account_id`.

Choose the latest invoice by:

1. `invoice_date DESC`
2. `invoice_id DESC` as deterministic tie-breaker

Expose current account attributes needed for account questions:

- account_id, account_name, region, plan
- seats, discount_pct, churned
- csat_score, support_tickets
- subscription_mrr_usd
- latest_invoice_date

Caveat: `account_id` is used because the supplied data dictionary defines it as the account key, but the source shows key-integrity problems. Attach this caveat to account-level answers.

### `v_account_metrics`

Grain: one row per `account_id`.

Join current attributes to lifetime invoice metrics.

Expose:

- current attributes from `v_account_current`
- `lifetime_net_revenue_usd`: sum recognized revenue for the account
- `current_mrr_usd`: current `subscription_mrr_usd` when `churned=false`, otherwise null
- `is_active`: not churned

This view supports:

- average MRR by region over active accounts
- churn rate by current plan
- top accounts by lifetime net revenue with current CSAT
