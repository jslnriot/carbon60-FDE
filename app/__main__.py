"""CloudNova command-line interface."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.agent import AgentError, answer_question
from app.format import format_answer
from app.pipeline import load_config, run_pipeline


def _build(config_path: str | Path) -> None:
    metrics = run_pipeline(config_path)
    print(
        "Built CloudNova model: "
        f"{metrics['output_invoice_count']} invoices, "
        f"{metrics['total_quarantined_rows']} quarantined."
    )


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app")
    parser.add_argument("--config", default="config.yaml")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("build", help="Rebuild the deterministic DuckDB model.")
    ask = commands.add_parser("ask", help="Ask a question through constrained SQL.")
    ask.add_argument("question")
    args = parser.parse_args()

    try:
        if args.command == "build":
            _build(args.config)
            return 0

        config, base = load_config(args.config)
        database = base / config["paths"]["database"]
        if not database.exists():
            _build(args.config)
        print(format_answer(answer_question(args.question, args.config)))
        return 0
    except (AgentError, KeyError, OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
