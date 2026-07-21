# Candidate improvements — text-to-SQL accuracy

**Status:** Backlog of candidates — **not approved specs, nothing implemented.**
**Source:** Google Cloud, *"Techniques for improving text-to-SQL"*
(https://cloud.google.com/blog/products/databases/techniques-for-improving-text-to-sql),
mapped against this codebase.
**Date:** 2026-07-20 · **Owner:** rhuanca@gmail.com

**How to use:** this doc maps the article's 13 techniques onto what we already do and what we
could add. Each candidate below (C1–C6) is a *feature idea*; when we pick one, it graduates to a
`specs/NNN-<slug>/spec.md` (spec-lite) with its own plan. See [README.md](README.md).

---

## Already covered (the article validates these)
We're ahead of the article on several of its techniques:

| Article technique | Where we already do it |
|---|---|
| Semantic layer over raw data (#4) | The whole design — `semantic/model.py`, the *virtual table* the LLM writes SQL over |
| Self-correction / validate-and-reprompt (#9) | Bounded repair loop — `engine.ask` (`engine.py:122`, `max_retries=1`): validator error is fed back to re-plan |
| In-context examples + data sampling (#2, #3) | `verified_queries`, `synonyms`, and `sample_values` injected by `build_system_prompt` (`planner.py:119`, `:162`) |
| Continuous evaluation (#13, partial) | `eval/history.jsonl` + the Evals trend view (spec 009), `eval/history.py` |
| SQL-aware foundation model (#8) | Claude Opus/Sonnet (strong on SQL) |
| Entity resolution — *field level* (#7, partial) | Metric/dimension **synonyms** resolve wording → canonical field |

---

## Candidate improvements

### C1 · Proactive disambiguation  *(article #6)*
- **Current state:** we clarify *after the fact* — `missing_dimensions` (`chat/app.py:116`) flags a
  breakdown the answer dropped, and the rewriter decontextualizes follow-ups (`rewriter.py`). But
  an ambiguous question is answered with a guess (or a silently-dropped constraint — e.g. the "year
  to date" that got ignored before we added `period_to_date`).
- **Gap:** no *pre-answer* clarifying question.
- **Suggestion:** detect ambiguity (a "top N" with no measure; a term matching 2+ metrics; a filter
  value not found — see C2) and let the planner emit **one clarifying question** instead of SQL —
  e.g. "shoes by quantity or revenue?" A new planner tool/branch (`emit_clarification`) + a UI
  prompt; the answer feeds the next plan.
- **Impact:** high (prevents confidently-wrong answers). **Effort:** medium. **Risk:** low.
- **Notes:** the natural next step in the honesty thread that produced `missing_dimensions` and the
  YTD fix.

### C2 · Value / entity resolution  *(article #3, #7)*
- **Current state:** field **synonyms** are resolved, but filter **literals** are passed through
  verbatim by the LLM (`WHERE product_name = 'cappucino'`). A typo or a wrong category value returns
  an **empty result with no explanation**.
- **Gap:** no value-level resolution against the real column values.
- **Suggestion:** resolve filter literals against actual values — a lightweight `DISTINCT`/sample
  lookup (or the model's `sample_values`) + fuzzy match — then either auto-correct
  (`'cappucino'→'Cappuccino'`) or surface *"no match for 'X' — did you mean 'Y'?"*. Feed candidate
  values into the prompt and/or validate post-plan.
- **Impact:** high (kills a common silent-empty-result failure). **Effort:** medium. **Risk:** low
  (read-only lookups; cache per column).
- **Notes:** pairs with C1 (unresolved value → clarifying question).

### C3 · Self-consistency voting  *(article #10)*
- **Current state:** one plan + one repair attempt.
- **Gap:** no multi-candidate agreement.
- **Suggestion:** for higher-stakes/ambiguous questions, sample **K** candidate SQLs (temperature or
  prompt variants), compile+run each, and pick by **result-set agreement** (majority). **Reuses
  `eval.scorer.result_sets_match`** (`scorer.py:105`) — the multiset compare already exists.
- **Impact:** medium-high (accuracy on hard questions). **Effort:** medium. **Risk:** cost/latency —
  gate on an ambiguity score or a per-query flag so it's not always-on.

### C4 · Retrieval / example selection  *(article #1, #2, #5)*
- **Current state:** `build_system_prompt` dumps the **entire** model (all dimensions + metrics) and
  **all** verified queries into every prompt (`planner.py:162`). Fine for the demo models.
- **Gap:** won't scale to a real client model (hundreds of fields, many verified queries) — cost and
  prompt noise; and successful past queries aren't reused.
- **Suggestion:** when the model is large, **retrieve the top-K relevant fields + examples** by
  semantic similarity to the question (embeddings) instead of the full dump; and **mine `traces.db`**
  (`trace/store.py`) for successful past queries as dynamic few-shots — closing the
  observability→improvement loop (article #5).
- **Impact:** high for large/real client models (low for the demo). **Effort:** high (adds an
  embedding/index dependency). **Risk:** medium — keep the full-dump path for small models.

### C5 · LLM-as-a-judge + broader synthetic benchmark  *(article #11, #12)*
- **Current state:** eval is deterministic — execution accuracy (`result_sets_match`) + IR-component
  F1; **ADR-0005** deliberately deferred an LLM judge.
- **Gap:** no judge for the fuzzy outputs (prose summaries, chart choice, or NL→SQL equivalences the
  strict multiset compare rejects); eval case coverage is small and hand-written.
- **Suggestion:** add an **LLM judge** for the fuzzy parts, and expand `eval/cases.yml` with
  synthetic cases across query complexity + the **new time-window kinds** (trailing / YTD / QTD /
  MTD) + dialects.
- **Impact:** medium (quality visibility). **Effort:** medium. **Risk:** low.
- **⚠️ Evals are the regression contract (ADR-0005 / memory):** any change to eval cases/harness
  gets called out and signed off first — never edited silently to pass.

### C6 · User-feedback signal  *(article #13)*
- **Current state:** every turn is logged to `traces.db` and quality is tracked offline
  (`eval/history.jsonl` + Evals trend), but there's **no user signal**.
- **Gap:** we can't see real-world satisfaction, only offline eval.
- **Suggestion:** capture **👍/👎** (and optionally "right period/breakdown?") per turn into
  `traces.db`, and chart a satisfaction metric beside the eval trend. Doubles as a source of C4's
  example mining (thumbs-up queries are good few-shots).
- **Impact:** medium (closes the continuous-eval loop). **Effort:** low-medium. **Risk:** low.

---

## Prioritization

| # | Candidate | Impact | Effort | Fit with current direction |
|---|---|---|---|---|
| C1 | Proactive disambiguation | High | Med | ★★★ extends the honesty work (missing_dims, YTD) |
| C2 | Value / entity resolution | High | Med | ★★★ kills silent empty results |
| C4 | Retrieval / example selection | High* | High | ★★ needed to scale to large client models |
| C3 | Self-consistency voting | Med-High | Med | ★★ reuses the eval scorer |
| C6 | User-feedback signal | Med | Low-Med | ★★ closes the observability loop |
| C5 | LLM-judge + more eval cases | Med | Med | ★ evals are the contract — sign-off first |

\* C4's impact is high for real/large client models, low for the demo.

**Recommended order:** **C1 + C2 first** — highest value and the tightest fit with the honesty
direction (don't guess, don't silently return empty). **C4** when we onboard a client with a large
model. **C3 / C6 / C5** follow.
