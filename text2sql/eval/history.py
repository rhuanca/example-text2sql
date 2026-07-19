"""Persist a per-run eval scorecard to a committed JSONL history, so agent quality
can be tracked over time — is the product improving or regressing? The history lives in
git (`eval/history.jsonl`) so the trend travels with the repo. Pure + stdlib only."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


def _git_sha() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5, check=True)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


@dataclass
class Scorecard:
    """One eval run's headline numbers for a single model."""

    timestamp: str
    git_sha: str
    model: str
    exec_accuracy: float
    n_pass: int
    n_total: int
    f1_metrics: float
    f1_dimensions: float
    f1_filters: float

    @classmethod
    def from_report(cls, report, model_name: str, timestamp: str | None = None,
                    git_sha: str | None = None) -> "Scorecard":
        """Build from an eval `Report` (runner.py) + the model's name."""
        return cls(
            timestamp=timestamp or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            git_sha=git_sha or _git_sha(),
            model=model_name,
            exec_accuracy=round(report.exec_accuracy, 4),
            n_pass=report.n_passed,
            n_total=report.total,
            f1_metrics=round(report.mean_metric_f1, 4),
            f1_dimensions=round(report.mean_dimension_f1, 4),
            f1_filters=round(report.mean_filter_f1, 4),
        )

    def to_dict(self) -> dict:
        return asdict(self)


def append_scorecard(path, sc: Scorecard) -> None:
    """Append one scorecard as a JSON line (the history grows one run at a time)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps(sc.to_dict()) + "\n")


def load_history(path) -> list[dict]:
    """All recorded scorecards in append order; [] when there is no history yet."""
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
