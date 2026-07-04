"""CLI: run the evaluation suite and print a report.

    uv run python -m text2sql.eval.run                      # measure the real planner
    uv run python -m text2sql.eval.run --cases path/to/cases.yml
    uv run python -m text2sql.eval.run \\
        --cases eval/cases_qbo.yml --model models/qbo.yml --db demo_qbo.db

Runs the real LLM planner (needs ANTHROPIC_API_KEY) against the committed cases
and scores its accuracy. --cases/--model/--db default to the sales demo.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..db.seed import build_database as build_sales_db
from ..db.seed_qbo import build_database as build_qbo_db
from ..engine.dialects.sqlite import SqliteDialect
from ..engine.executor import SqliteExecutor
from ..engine.planner import AnthropicPlanner
from ..semantic.model import load_model
from .dataset import load_cases
from .report import format_report
from .runner import run_suite

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES = REPO_ROOT / "eval" / "cases.yml"
DEFAULT_MODEL = REPO_ROOT / "models" / "sales.yml"
DEFAULT_DB = REPO_ROOT / "demo.db"

# Which seeder builds each model's synthetic DB, keyed by model filename. Used
# only to auto-create the DB when it is absent (e.g. a fresh CI checkout).
SEEDERS = {
    "sales.yml": build_sales_db,
    "qbo.yml": build_qbo_db,
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the text2sql eval suite.")
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--model", default=str(DEFAULT_MODEL))
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument(
        "--min-accuracy",
        type=float,
        default=None,
        help="Exit non-zero if execution accuracy falls below this (0..1). "
        "Use as a CI regression gate.",
    )
    args = parser.parse_args(argv)

    cases = load_cases(args.cases)
    model = load_model(args.model)
    db_path = Path(args.db)
    if not db_path.exists():
        seeder = SEEDERS.get(Path(args.model).name)
        if seeder is None:
            print(
                f"no seeder registered for model {args.model!r}; seed {db_path} first.",
                file=sys.stderr,
            )
            return 2
        seeder(db_path)

    planner = AnthropicPlanner()

    report = run_suite(cases, planner, model, SqliteDialect(), SqliteExecutor(str(db_path)))
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
