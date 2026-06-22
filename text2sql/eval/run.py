"""CLI: run the evaluation suite and print a report.

    uv run python -m text2sql.eval.run                  # mock planner (harness self-check)
    uv run python -m text2sql.eval.run --planner anthropic
    uv run python -m text2sql.eval.run --cases path/to/cases.yml

The mock planner replays each case's expected IR, so it reports 100% — it
exercises the harness end to end. Point ``--planner anthropic`` at the real LLM
(needs ANTHROPIC_API_KEY) to measure actual planner accuracy.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..db.seed import build_database
from ..engine.dialects.sqlite import SqliteDialect
from ..engine.executor import SqliteExecutor
from ..engine.planner import AnthropicPlanner, MockPlanner
from ..semantic.model import load_model
from .dataset import load_cases
from .report import format_report
from .runner import run_suite

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES = REPO_ROOT / "eval" / "cases.yml"
MODEL_PATH = REPO_ROOT / "models" / "sales.yml"
DB_PATH = REPO_ROOT / "demo.db"


def _mock_planner(cases):
    rules = [(c.question, c.expected.to_dict()) for c in cases]
    rules.sort(key=lambda kv: len(kv[0]), reverse=True)
    return MockPlanner(rules)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the text2sql eval suite.")
    parser.add_argument("--planner", choices=["mock", "anthropic"], default="mock")
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument(
        "--min-accuracy",
        type=float,
        default=None,
        help="Exit non-zero if execution accuracy falls below this (0..1). "
        "Use as a CI regression gate.",
    )
    args = parser.parse_args(argv)

    cases = load_cases(args.cases)
    model = load_model(MODEL_PATH)
    if not DB_PATH.exists():
        build_database(DB_PATH)

    if args.planner == "anthropic":
        planner = AnthropicPlanner()
    else:
        planner = _mock_planner(cases)

    report = run_suite(cases, planner, model, SqliteDialect(), SqliteExecutor(DB_PATH))
    print(format_report(report))

    if args.min_accuracy is not None and report.exec_accuracy < args.min_accuracy:
        print(
            f"\nFAIL: execution accuracy {report.exec_accuracy:.0%} is below the "
            f"required {args.min_accuracy:.0%}.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
