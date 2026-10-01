# Prompt and Review Log

This is a concise factual record of the implementation prompts, reviews, and
corrections. It is not a verbatim transcript.

## 1. Pipeline and semantic views

The initial request required inspection of the data-model specification,
decisions, configuration, business context, CSV, and Cursor rules before any
code changes. Cursor proposed the module layout, three-stage deduplication,
DuckDB tables, semantic views, and validation checks, then stopped at the plan
gate.

After approval, the implementation added the deterministic pipeline, canonical
and audit tables, semantic views, catalog, tests, and generated DQ report.
Review confirmed that stage-one exact duplicate comparison excludes
`source_row` and retains the earliest source row.

## 2. Euro amount correction

The first strict implementation quarantined 258 EUR rows because the source
used the `€` symbol while the initial amount-format contract listed only `$`
and `£`. The source-data format was reviewed, the specification was corrected
to accept `€`, and the parser and regression test were updated. The final
pipeline has zero quarantined rows.

## 3. Decimal persistence correction

While comparing the rebuilt semantic result with the expected 2024 revenue,
review found that 294 Python `Decimal` values represented in scientific
notation were being mis-scaled through the pandas-to-DuckDB registration path.
The pipeline now serializes decimal fields in fixed-point form before DuckDB
casts them into declared DECIMAL columns. A persistence regression assertion
was added. The corrected view result is $27,793,767.37.

## 4. Query agent and guardrail

The agent work began with a plan gate covering the `QueryPlan` schema, provider
request flow, SQL validation, catalog-derived allowlist, CLI, and guardrail
tests. The implementation added:

- OpenAI-compatible structured JSON requests with Pydantic validation;
- configurable key, model, and optional base URL;
- one retry for provider request failures;
- sqlglot parsing and one-statement read-only enforcement;
- catalog-only view access, external-function rejection, and LIMIT capping;
- read-only DuckDB execution with external access disabled;
- deterministic result formatting with no second model call; and
- `python -m app build` / `python -m app ask`.

Required guardrail cases were exercised, including valid SELECT and CTE
queries, mutation and multi-statement rejection, raw-table and external
function rejection, and LIMIT insertion/capping.

## 5. Recognized-revenue semantic correction

The first successful DeepSeek revenue query filtered
`status = 'paid'` while summing `recognized_revenue_usd`. That produced gross
paid revenue of $29,565,429.39 and incorrectly excluded negative refunds.

Review identified `recognized_revenue_usd` as the authoritative net semantic
measure. Catalog descriptions and agent system rules were strengthened to say
that semantic measures already encode their status rules. The corrected query
sums `recognized_revenue_usd` for 2024 without a paid-status filter and returns
$27,793,767.37. Application formatting was also corrected to display USD
values to two decimal places.

## 6. Independent reference SQL

Reference queries were written independently of agent output for E1–E4 and
optional E7–E8. They query only trusted semantic views and produced the
stakeholder values reported in the README.

E5 has no reference SQL because it tests refusal behavior. E6 has no reference
SQL because it is the documented expected failure for aggregate row-level
provenance.

## 7. Eval harness

The eval harness loads case definitions, executes expected values from
`evals/reference.sql`, invokes the agent once per enabled case, compares
results, prints PASS / FAIL / XFAIL with reasons and latency, and exits nonzero
only for unexpected failures.

During development, DeepSeek output varied across runs. Observed examples
included an ambiguous SQL query and a refusal of the supported churn question.
The harness was adjusted only to compare equivalent result shapes and to
classify E6 as XFAIL; it does not retry cases to manufacture passing results.
The final required run reported E1–E5 PASS and E6 XFAIL.

Optional E7/E8 reference values exist, but those agent cases are not enabled in
the final automated suite because required-case behavior showed provider
variance during development.
