# Spec 003 — Evaluation Harness

Status: Draft
Owner: rhuanca@gmail.com
Date: 2026-06-20

## 1. Problem statement

The engine's only fuzzy step is the **planner**: NL → Semantic Query IR. Today we
trust it by eyeballing the chat UI and one gated smoke test. We have no way to
say "the planner is right 18/20 times" or to notice that a model/prompt/YAML
change made it *worse*.

This spec adds an **evaluation harness**: a committed dataset of natural-language
questions paired with their expected Semantic Query IR, plus a runner that feeds
each question through a planner, scores the result, and reports accuracy. It
turns planner quality from a vibe into a number and gives us a regression gate
for every future change to the model, the prompt, or the LLM.

This spec covers evaluation only. It does not change the engine's behavior.

## 2. Goals / Non-goals

### Goals
- A **dataset format** (`eval/cases.yml`): a list of cases, each with an id, a
  natural-language question, the expected IR, and optional tags/notes.
- A seed dataset of ~12–20 cases covering the model's metrics, dimensions,
  filters, time windows, and the budget-vs-actual fan-out case.
- A **scorer** (pure, unit-tested) that compares a predicted IR to an expected
  IR and yields:
  - an **exact match** flag (order-insensitive where order is irrelevant), and
  - **component scores** (precision/recall over metrics, dimensions, filters).
- **Execution accuracy**: compile both the predicted and expected IR and run
  them against the seeded SQLite, comparing result sets — the gold-standard
  metric that tolerates semantically-equivalent IRs.
- A **runner** that ties planner → score → (optionally) execute → aggregate, and
  a small **CLI** to run the suite and print a per-case + summary report.
- An **offline regression test** (unittest) using the `MockPlanner` so the suite
  stays green with no API key, plus thorough unit tests of the scorer itself.

### Non-goals (deferred)
- Benchmarking against external datasets (Spider, BIRD, etc.).
- Measuring or asserting latency / token cost.
- Auto-tuning the prompt or model from eval results.
- A web/visual dashboard — text report only.
- Grading the prose summary (Spec 002's summarizer); we grade the IR/SQL.

## 3. Approach

### 3.1 What we score, and why two metrics

The planner's output is an **IR**, so the most direct signal is **IR match**.
But two different IRs can be equally correct (e.g. `metrics: [a, b]` vs `[b, a]`,
or an extra harmless `order_by`). So we score on two axes:

1. **IR component match** — strict, structural, explains *what* differed. We
   normalize order where it doesn't matter (metrics, dimensions, and filters are
   treated as sets) and report precision/recall per component plus an overall
   exact-match flag. This pinpoints *which* part the planner got wrong.
2. **Execution accuracy** — run predicted-IR→SQL and expected-IR→SQL against the
   seeded DB and compare the returned rows. This is the standard text-to-SQL
   metric and forgives cosmetic IR differences that don't change the answer.

A case "passes" when its execution accuracy matches (rows equal). Component
scores are diagnostic. We report both.

### 3.2 Result-set comparison rules
- Compare the **set of rows** (a multiset) unless the expected IR has an
  `order_by`, in which case row order must match too.
- Column **set** must match (names), order-insensitive.
- Cast numeric cells to a canonical form so `5` and `5.0` compare equal.

### 3.3 Pipeline

```
   eval/cases.yml
        │  load cases (id, question, expected IR)
        ▼
   for each case:
        ├─ planner.plan(question)  ──►  predicted IR        (MockPlanner | AnthropicPlanner)
        ├─ score_ir(expected, predicted)  ──►  component P/R + exact flag
        └─ execution_match(expected, predicted, model, dialect, executor)
                 compile+run both, compare row sets  ──►  pass / fail
        ▼
   aggregate  ──►  CaseResult[]  +  summary (exec accuracy, exact-IR rate, per-component avg)
        ▼
   report (text): per-case table + totals
```

## 4. Repository layout (additions)

```
text2sql/
  eval/
    __init__.py
    dataset.py      # EvalCase dataclass + load_cases(path)
    scorer.py       # pure IR comparison + result-set comparison
    runner.py       # run_suite(cases, planner, model, dialect, executor) -> Report
    report.py       # format a Report as text
    run.py          # CLI: `python -m text2sql.eval.run [--planner mock|anthropic]`
eval/
  cases.yml         # the committed dataset
tests/
  test_eval_scorer.py    # scorer unit tests (no LLM, no DB needed for IR parts)
  test_eval_runner.py    # MockPlanner end-to-end over a few cases + report shape
specs/
  003-eval-harness/spec.md
```

## 5. Formats

### 5.1 `eval/cases.yml`

```yaml
cases:
  - id: dozen-glazed-wow
    question: "How is Dozen Glazed performing week over week?"
    tags: [filter, time-series, single-table]
    expected:
      metrics: [total_net_sales, units_sold]
      dimensions: [product_name, iso_year, iso_week]
      filters: [{ field: product_name, op: "=", value: "Dozen Glazed" }]
      order_by: [{ field: iso_week, dir: asc }]

  - id: sales-by-market
    question: "What were total net sales by market?"
    tags: [cross-table-join, categorical]
    expected:
      metrics: [total_net_sales]
      dimensions: [market]

  - id: budget-vs-actual
    question: "Budget vs actual by store"
    tags: [fan-out, multi-base]
    expected:
      metrics: [total_net_sales, total_budget]
      dimensions: [store_id]
```

`expected` is exactly the IR dict shape consumed by `SemanticQuery.from_dict`.

### 5.2 Report (in-memory + text)

- `CaseResult`: `id`, `question`, `passed` (exec match), `exact_ir` (bool),
  `ir_scores` (per-component precision/recall), `error` (if planning/compiling
  threw), and the predicted IR for inspection.
- `Report`: list of `CaseResult` + summary: execution accuracy (passed/total),
  exact-IR rate, and mean per-component precision/recall.

## 6. Key behaviors / acceptance criteria

1. **Dataset loads.** `load_cases("eval/cases.yml")` returns typed `EvalCase`
   objects; every `expected` parses via `SemanticQuery.from_dict`; a bad case id
   or unparseable IR is reported, not silently skipped.
2. **Scorer is pure and order-insensitive where it should be.** `metrics:
   [a,b]` vs `[b,a]` scores as a perfect component match; a missing/extra metric
   lowers recall/precision accordingly. Filters compare as a set of
   (field,op,value). Covered by unit tests, no LLM, no DB.
3. **Execution accuracy works.** Two IRs that yield the same rows count as a
   pass even if the IR text differs; two IRs that yield different rows fail.
   Numeric `5` vs `5.0` compare equal; row order is enforced only when the
   expected IR specifies `order_by`.
4. **Runner aggregates.** `run_suite(...)` over the seed dataset with the
   `MockPlanner` (rules written to satisfy the cases) yields a `Report` whose
   execution accuracy is 100% and whose per-case results carry component scores.
5. **Graceful failures.** If the planner returns an IR that fails validation or
   compilation, that case is recorded as `passed=False` with the error captured
   — one bad case never aborts the suite.
6. **CLI report.** `python -m text2sql.eval.run` prints a readable per-case line
   (id, pass/fail, exact-IR) and a summary footer. `--planner anthropic` uses
   the real planner when a key is present; default is `mock`.
7. **Offline & green.** The scorer and runner tests run fully offline with the
   mock planner. Any test that needs the real LLM is gated on an API key and
   skipped otherwise, consistent with the existing suite.

## 7. Testing strategy

- `unittest` only.
- `test_eval_scorer.py`: order-insensitivity, partial-credit precision/recall,
  exact-match flag, filter set comparison, and result-set comparison (set vs
  ordered, numeric normalization, column-set mismatch).
- `test_eval_runner.py`: load the real `eval/cases.yml`, run with a `MockPlanner`
  whose rules satisfy the cases, assert 100% execution accuracy and a
  well-formed `Report`; include one deliberately-wrong mock rule to assert a
  case fails and the error/score is captured.
- Optional: a gated test that runs a couple of cases through `AnthropicPlanner`
  when a key is present, asserting the harness produces a report (not a specific
  score).

## 8. Open questions

- Should `total_budget` requested without `total_net_sales` still be a valid
  "budget vs actual" case, or do we always pair them? (Lean: dataset encodes the
  intended pairing; scorer just compares.)
- Do we want a JSON report output for CI ingestion now, or defer until there's a
  CI consumer? (Lean: defer — text report only, per non-goals.)

## 9. Outcome

Implemented as tasks E1–E7 (see `tasks.md`). The harness lives in
`text2sql/eval/` (`dataset.py`, `scorer.py`, `runner.py`, `report.py`, `run.py`)
with the dataset in `eval/cases.yml` (18 cases). Both scoring axes are in place:
execution accuracy (compile+run both IRs against the seeded SQLite, compare row
sets) is the pass/fail signal, and IR component precision/recall + an exact-match
flag are diagnostic. `python -m text2sql.eval.run` prints a per-case + summary
report; `--planner anthropic` measures the real LLM. 25 new unit tests (83 total)
pass; the mock-planner self-check reports 100% and keeps the suite green offline.
No engine code changed — the harness only consumes the existing planner,
compiler, validator, and executor.

Both §8 open questions resolved as leaned: budget cases pair `total_budget` with
`total_net_sales` in the dataset; report output is text-only (JSON deferred until
a CI consumer exists).
