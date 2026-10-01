CloudNova FDE Take-Home — Task List
Goal
Build a small, production-shaped CloudNova finance Q&A system where:
The model interprets the question; deterministic code owns the financial rules.
The finished system should:
- clean and model the dirty invoice export deterministically,
- expose trusted semantic views in DuckDB,
- translate plain-English finance questions into SQL,
- validate generated SQL before execution,
- return the result together with the generated SQL, assumptions, rules, and caveats,
- include an independent eval harness with visible PASS / FAIL / XFAIL results,
- document important data-quality findings, design decisions, limitations, and AI usage.
Primary demo command:
python -m app ask "What was our total recognized revenue in USD for paid invoices in 2024?"
Primary eval command:
python -m evals.run_evals
1. Repository + source data
- [x] Create Git repository
- [x] Create project directories
- [x] Add data/cloudnova_invoices.csv
- [ ] Ensure business context is a real plain-text file at data/BUSINESS_CONTEXT.md
- [x] Create Python virtual environment
- [x] Install requirements.txt
2. Project configuration + Cursor rules
- [x] Add .env.example
- [x] Add config.yaml
- [x] Add requirements.txt
- [ ] Verify these files physically exist:
  - [ ] .cursor/rules/00-project.mdc
  - [ ] .cursor/rules/10-python-data.mdc
  - [ ] .cursor/rules/20-llm-sql.mdc
3. Specifications + decisions
- [x] Add specs/data-model.md
- [x] Add specs/query-agent.md
- [x] Add specs/evals.md
- [x] Add docs/decisions.md
The specs are the implementation contract. Cursor should not modify them unless explicitly instructed.
4. Cleaning pipeline + semantic views
Plan
- [x] Ask Cursor to inspect specs/data/config and propose a plan before coding
- [x] Review Cursor's proposed plan
- [x] Fix missing Cursor-rule paths before implementation
- [x] Ensure data/BUSINESS_CONTEXT.md exists as Markdown
- [x] Approve Cursor's plan with the exact-duplicate clarification:
  - exact duplicate comparison must exclude source_row
Implementation
Cursor may create/modify only:
- [x] app/__init__.py
- [x] app/pipeline.py
- [x] models/views.sql
- [x] models/catalog.yaml
- [x] tests/test_pipeline.py
- [x] docs/dq_report.md
Required pipeline behavior
- [x] Read CSV with dtype=str, keep_default_na=False
- [x] Preserve literal region NA
- [x] Add one-based source_row
- [x] Remove exact duplicates using original source columns, not source_row
- [x] Normalize categories from config.yaml
- [x] Parse/validate amounts, numerics, dates, booleans, and emails
- [x] Quarantine hard failures with reason codes
- [x] Apply configured FX division rule
- [x] Calculate amount reconciliation
- [x] Calculate subscription MRR deterministically
- [x] Flag future-dated and before-signup invoices
- [x] Remove normalization-only duplicates
- [x] Resolve remaining invoice conflicts by configured status precedence
- [x] Log resolved conflicts to dq_conflicts
- [x] Create raw_invoices, invoices, quarantine, and dq_conflicts
- [x] Create:
  - [x] v_revenue_lines
  - [x] v_account_current
  - [x] v_account_metrics
- [x] Generate docs/dq_report.md
Step 4 validation
- [x] pytest -q passes
- [x] Raw row count is 5,125
- [x] Final invoice count is 5,000, or any difference is explained
- [x] Literal NA region rows remain present
- [x] Amount reconciliation is effectively 100%, or any exception is explained
- [x] Duplicate/conflict counts are reported rather than hidden
- [x] Quarantine count is reported
- [x] Future-dated count is reported
- [x] Invoice-before-signup count is reported
- [x] Direct SQL against v_revenue_lines produces the 2024 recognized-revenue result
- [x] Any difference from the prior ~$27,793,767.37 cross-check is investigated and documented, not forced
5. Natural-language query agent + SQL guardrail
Implementation
- [x] Create app/agent.py
- [x] Create app/guardrail.py
- [x] Create app/format.py
- [x] Create app/__main__.py
- [x] Create tests/test_guardrail.py
Required behavior
- [x] python -m app build
- [x] python -m app ask "QUESTION"
- [x] OpenAI returns structured query-plan output
- [x] LLM queries only semantic views
- [x] LLM does not calculate financial metrics
- [x] SQL parsed with sqlglot
- [x] Exactly one read-only SELECT / WITH...SELECT
- [x] Non-allowlisted tables rejected
- [x] Mutation SQL rejected
- [x] LIMIT inserted/capped
- [x] DuckDB opened read-only with external access disabled
- [x] Output shows answer/result, generated SQL, assumptions, views/rules, caveats
Step 5 validation
- [x] Normal SELECT accepted
- [x] DROP TABLE rejected
- [x] Multiple statements rejected
- [x] Non-allowlisted table rejected
- [x] Missing LIMIT injected
- [x] Excessive LIMIT capped
- [x] Real revenue question works end-to-end
- [x] Unsupported cause question returns answerable=false
6. Independent eval harness
Reference values
- [x] Create evals/reference.sql
- [x] Reference SQL is independent of agent-generated SQL
- [x] Run reference queries directly against semantic views
Eval runner
- [x] Create evals/__init__.py
- [x] Create evals/cases.yaml
- [x] Create evals/run_evals.py
Core cases
- [x] E1 — 2024 recognized revenue
- [x] E2 — highest average MRR region
- [x] E3 — Enterprise vs Starter churn
- [x] E4 — refunds
- [x] E5 — unsupported cancellation-cause question
- [x] E6 — expected failure for aggregate row-level provenance
Validation
- [x] python -m evals.run_evals
- [x] PASS / FAIL / XFAIL displayed clearly
- [x] E6 is intentionally XFAIL
- [x] Unexpected failures produce non-zero exit status
Optional if time remains
- [ ] E7 — top 5 accounts
- [ ] E8 — pending/failed exposure
- [ ] E9 — malicious mutation request
7. Documentation
- [x] Finish README.md
- [x] Create docs/architecture.md
- [x] Create/finish docs/prompt-log.md
README should include:
- [x] short customer-facing explanation
- [x] stakeholder answers near the top
- [x] FX finding
- [x] account-key caveat
- [x] duplicate-status conflict finding
- [x] quickstart
- [x] architecture explanation
- [x] specs/decisions links
- [x] actual eval results
- [x] known limitations/failure modes
- [x] questions for the finance lead
- [x] how AI was used
- [x] what another day would add
- [ ] actual time spent
8. Fresh-clone verification
- [ ] Clone repo into a new directory
- [ ] Create fresh venv
- [ ] Install requirements
- [ ] Configure .env
- [ ] Follow README exactly
- [ ] python -m app build succeeds
- [ ] Example python -m app ask ... succeeds
- [ ] python -m evals.run_evals succeeds as documented
9. Final safety + submission checks
- [ ] .env is not tracked
- [ ] *.duckdb is not tracked
- [ ] No API keys exist anywhere in Git history
- [ ] Architecture diagram matches actual file/view names
- [ ] Specs still reflect implementation
- [ ] Prompt log contains real prompts and real corrections only
- [ ] Repo is accessible to OpsGuru reviewers
- [ ] Final submission email contains repo link and short summary
Current focus
Step 7 — fill in actual time spent, then complete fresh-clone verification.