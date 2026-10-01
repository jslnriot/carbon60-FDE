# CloudNova Finance Q&A

CloudNova can ask finance questions in plain English while deterministic code
owns the financial rules. The language model interprets intent and proposes
SQL; tested transformations and semantic views calculate revenue, MRR, churn,
refunds, and exposure.

## Answers to CloudNova's questions

These values come from the cleaned 5,000-invoice model and independent
reference SQL:

- **2024 recognized revenue:** **$27,793,767.37**. This is net recognized
  revenue: paid invoices contribute positively and refunds contribute
  negatively.
- **Highest average MRR region:** **APAC — $6,147.30** per active account.
- **Refunds:** **$8,200,645.85** returned all time.
- **Churn by current plan:**
  - Enterprise: **171 / 780 = 21.92%**
  - Starter: **354 / 1,882 = 18.81%**
- **Top accounts by lifetime net revenue:**
  1. `ACC-9893` — **$845,500.00** — CSAT unknown
  2. `ACC-3142` — **$764,777.50** — CSAT 2, flagged
  3. `ACC-1214` — **$759,750.00** — CSAT 5
  4. `ACC-6728` — **$757,400.00** — CSAT 4
  5. `ACC-5719` — **$753,405.00** — CSAT 5
- **Pending/failed exposure:** **903 invoices — $26,611,016.45**.

E1–E6 are enabled in the automated agent eval suite. The E7 top-account and E8
exposure values above were independently calculated by `evals/reference.sql`,
but those optional questions are not enabled in `evals/cases.yaml`.

Account-level results use `account_id` because the supplied dictionary defines
it as the account key. The source has key-integrity problems, so those results
carry an account-key caveat.

## What I found in the data

- 5,125 raw rows became 5,000 canonical invoices.
- 82 exact duplicates were removed using only original source columns.
- 27 same-invoice rows became identical after normalization and were removed.
- 16 conflicting invoice records were resolved with the configured
  conservative status precedence.
- 0 rows were quarantined in the final pipeline.
- 5,000 of 5,000 invoice amounts reconciled to subscription terms: **100%**.
- The written FX direction conflicts with the generated invoice amounts.
  Dividing local amounts by the configured rates reconciles the ledger;
  multiplying does not. This is a prototype data-contract finding to confirm
  with Finance, not a general FX convention.
- `account_id` has integrity problems, so account-level answers include a
  caveat rather than silently replacing it with `account_name`.
- Date interpretation materially changes anomaly counts. Under the documented
  day-first rule for `NN-NN-YYYY`, invoice-before-signup count is **0**. An
  earlier spreadsheet-style month-first interpretation produced **16**.
- 154 future-dated invoices are flagged and retained.

The generated data-quality report is in
[`docs/dq_report.md`](docs/dq_report.md).

## Quickstart

Requires Python 3.10+.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Configure `.env` with your own credentials and provider values:

```dotenv
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://your-openai-compatible-api.example
OPENAI_MODEL=your-model-name
```

`OPENAI_BASE_URL` is optional for OpenAI and supports providers such as
DeepSeek through their OpenAI-compatible API. No provider URL or model name is
hardcoded in the application.

Build the deterministic model:

```bash
python -m app build
```

Ask a question:

```bash
python -m app ask "What was our total recognized revenue in USD for paid invoices in 2024?"
```

If the configured DuckDB database does not exist, `ask` builds it first.

Run tests and evaluations:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q
python -m evals.run_evals
```

The pytest environment variable isolates the project from unrelated globally
installed pytest plugins; it does not change application behavior.

## Architecture

> **The model interprets the question; deterministic code owns the financial
> rules.**

This is Track B: a deterministic data model plus constrained natural-language
to SQL. Financial questions require exact filtering, grouping, reconciliation,
and aggregation. RAG or vector retrieval can find relevant text, but it does
not make arithmetic or accounting semantics trustworthy.

The system has four boundaries:

1. `app/pipeline.py` reads the dirty export as strings, normalizes and validates
   records, performs the three-stage deduplication, computes deterministic
   measures, writes DuckDB tables, and generates the DQ report.
2. `models/views.sql` exposes invoice and account semantic views.
   `models/catalog.yaml` documents their grain, measures, and caveats and is
   also the query allowlist.
3. `app/agent.py` asks an OpenAI-compatible model for a structured `QueryPlan`.
   The model receives the question and catalog; it does not calculate results.
4. `app/guardrail.py` parses SQL with sqlglot, permits one read-only query over
   catalog views, caps row count, and executes through read-only DuckDB with
   external access disabled. `app/format.py` formats the returned values
   without a second model call.

See [`docs/architecture.md`](docs/architecture.md) for the full flow.

## Contracts and decisions

- [Data model specification](specs/data-model.md)
- [Query agent specification](specs/query-agent.md)
- [Evaluation specification](specs/evals.md)
- [Locked implementation decisions](docs/decisions.md)
- [Semantic catalog](models/catalog.yaml)
- [Independent reference SQL](evals/reference.sql)

## Evaluation

The final required eval run produced:

- **E1 PASS** — 2024 recognized revenue matched independent SQL within $0.01.
- **E2 PASS** — APAC and its average MRR matched.
- **E3 PASS** — Enterprise and Starter churn rates matched.
- **E4 PASS** — refund total matched within $0.01.
- **E5 PASS** — the unsupported cancellation-cause question was marked
  unanswerable.
- **E6 XFAIL** — v1 does not provide full invoice-level provenance for an
  aggregate answer.

Expected values are executed from `evals/reference.sql`, independently of SQL
generated by the agent. The eval runner exits nonzero only for unexpected
failures.

DeepSeek output is not deterministic. Intermediate eval runs included an
ambiguous generated query and a refusal of an answerable churn question; later
runs produced correct plans. The harness reports such variance rather than
retrying cases to make them pass.

## Known limitations and failure modes

- `account_id` is not a reliable real-world customer master key.
- There is no authoritative source or update timestamp for conflicting invoice
  statuses.
- The conflict precedence `refunded > void > failed > pending > paid` is a
  conservative prototype decision.
- Natural-language interpretation is single-turn; there is no clarification
  conversation.
- DeepSeek or another configured model may produce different SQL across runs.
- Aggregate answers do not yet provide invoice-level citations.
- Refund rows cannot be attributed back to an original invoice period.
- Future-dated invoices are flagged but retained in metrics.
- SQL guardrails reduce risk, but they are not a complete security boundary.

## Questions for the finance lead

1. Is division the intended FX direction for this export?
2. Which source system is authoritative when invoice statuses conflict?
3. Can Finance provide a master customer key to replace or validate
   `account_id`?
4. Should future-dated invoices remain in current analytical results?
5. When stakeholders say “revenue,” do they mean gross paid invoices or net
   recognized revenue?
6. Should refunds net in the refund period or be attributed to the original
   invoice period?

## How AI was used

- Specifications and Cursor rules were created before implementation.
- Cursor implemented narrowly scoped modules from those contracts, with a plan
  gate before file changes.
- Pipeline and query results were reviewed before proceeding to later stages.
- DeepSeek translates natural-language questions into constrained SQL.
- Deterministic DuckDB views—not the model—calculate financial results.

Genuine review and correction examples:

- The source contained `€` amounts omitted from the initial parsing spec, so
  the parsing contract was corrected before accepting those rows.
- Decimal persistence through pandas into DuckDB incorrectly scaled some
  scientific-notation values; fixed-point serialization corrected it.
- The first DeepSeek revenue query added `status = 'paid'`, yielding gross paid
  revenue and excluding refunds. Catalog and system-prompt semantics were
  strengthened so `recognized_revenue_usd` remains authoritative.
- Model variance was observed and surfaced while developing the eval harness.

See [`docs/prompt-log.md`](docs/prompt-log.md) for the concise work record.

## What I would do with another day

- Add a clarification turn for ambiguous questions.
- Add row-level provenance and invoice citations.
- Integrate an authoritative account master and source-system metadata.
- Add CI for tests and evals.
- Expand provider/model evaluation and variance tracking.

## Time spent

Approximately 1.45 hours of active work.

I intentionally stopped at a narrow, working vertical slice rather than
expanding the scope. The extra time beyond the suggested one-hour target went
primarily to validating data-quality assumptions, correcting source-data edge
cases, and building the eval/guardrail artifacts requested in the rubric.