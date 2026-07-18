# Conversation memory

Short-term memory that lets the assistant resolve **follow-up** questions
("only for 2026", "break that down by class") by giving the planner the last few
turns. It is intentionally minimal: in-session only, no persistence, no
summarization.

The one invariant it must not break: **everything from the IR onward stays
deterministic.** Memory is extra *context* the LLM sees; the planner still emits
only validated semantic SQL (`emit_sql`), parsed and checked against the model
before anything runs.

## How scope carries: a query-rewrite step

Carryover is **not** left to the planner's judgment. Before planning, an optional
`Rewriter` (`engine/rewriter.py`, wired in the chat app) **decontextualizes** the
follow-up into a standalone question using the recent turns — carrying forward the
active scope (a pinned entity, time range, classification) unless the user names a
different value, **broadens** ("all", "overall", "combined"), or **clears** ("reset",
"ignore that"). The planner then plans that self-contained question, so a filter like
`entity = 'Contoso SAS'` survives a new-metric follow-up ("revenue of the past 6
days") instead of silently vanishing. This mirrors Snowflake Cortex Analyst's design.

```text
"revenue of the past 6 days"  + prior scope entity = 'Contoso SAS'
        → AnthropicRewriter →  "revenue of the past 6 days for Contoso SAS"  → planner
```

The rewritten question is exposed on `Result.rewritten` and shown in the UI as
"Interpreted as: …", so a carried (or mistakenly over-attached) scope is visible and
reversible. `Engine.ask` runs the rewrite only when a rewriter is configured **and**
there is history; otherwise it falls back to threading `history` into the planner's
prompt via `_history_block` (below) — the mechanism used by eval stubs and tests.

## What is remembered

Two different representations of "history" coexist — don't confuse them:

| | `st.session_state.history` | `recent_turns(...)` output |
|---|---|---|
| Purpose | redraw the chat transcript | the planner's memory |
| Shape | `{role, text}` and `{role, result, summary}` / `{role, error}` | `[{"question": str, "ir": dict}]` |
| Contents | full `Result` objects (sql, rows, columns…) | just the question + the **IR** that turn produced |
| Size | whole session | capped at the **last 4 answered turns** |
| Error turns | kept (rendered as an error) | **excluded** (no IR to build on) |

We carry the **IR, not the result rows**: the IR is the compact record of *what
was computed* and is exactly what a refinement builds on; rows would be large and
irrelevant. `recent_turns` is a pure function (`text2sql/chat/app.py`) so it is
unit-tested without Streamlit.

## Round-trip (one follow-up turn)

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant App as app.py (Streamlit)
    participant SS as st.session_state.history
    participant Eng as Engine.ask
    participant RW as AnthropicRewriter
    participant Pl as AnthropicPlanner.plan
    participant DB as SQLite

    Note over U,SS: prior turn scoped to entity = 'Contoso SAS'
    U->>App: "revenue of the past 6 days"
    App->>SS: recent_turns(history, limit=4)
    SS-->>App: [{question, ir}, …]  (answered turns only, IR not rows)
    Note over App,SS: computed BEFORE appending the new prompt,<br/>so the question is not fed back to itself
    App->>SS: append {role:"user", text}
    App->>Eng: ask(question, history=history)

    Eng->>RW: rewrite(question, history)
    Note over RW: decontextualize — carry prior scope forward<br/>unless the user broadens / clears it
    RW-->>Eng: "revenue of the past 6 days for Contoso SAS"

    rect rgb(238,244,255)
    loop repair loop — max_retries+1 attempts
        Eng->>Pl: plan(standalone question, model, error, history=None)
        Note over Pl: question already stands alone,<br/>so no history block is needed
        Pl-->>Eng: semantic SQL → parse + validate → IR
        Eng->>Eng: validate_ir + compile  (deterministic)
        Eng->>DB: run(sql, params)
        DB-->>Eng: columns, rows
    end
    end

    Eng-->>App: Result(ir, sql, …, rewritten)
    App->>SS: append {role:"assistant", result}
    Note over SS: this turn's IR becomes memory for the next turn
```

## How history reaches the prompt

`_history_block(history)` (`text2sql/engine/planner.py`) renders the turns into a
compact block that is **prepended to the user message** — the static system
prompt is left untouched:

```text
CONVERSATION SO FAR (most recent last). Use it to resolve follow-up questions
like 'and for 2026' or 'break that down by class': carry over the previous
metrics/dimensions/filters unless the user changes them.
Q: show revenue by month
Computed: {"metrics": ["total_amount"], "dimensions": ["txn_month"]}

Current question: only for 2026
```

The model then emits an IR that reuses the prior metric/dimensions and adds a
`txn_year = 2026` filter. Verified end to end against the live model:

```text
Q1 "show revenue by month" → dims [txn_year, txn_month], filter classification=Revenue   (24 rows)
Q2 "only for 2026"         → same dims + filter, PLUS txn_year = 2026                     (12 rows)
```

## Call path (who owns what)

```mermaid
flowchart LR
    subgraph UI["chat/app.py  (per-session state)"]
        H["st.session_state.history<br/>render log"]
        RT["recent_turns()<br/>pure · cap 4 · skip errors"]
        H --> RT
    end
    subgraph ENG["engine/"]
        A["Engine.ask(question, history)"]
        RW["AnthropicRewriter.rewrite()<br/>→ standalone question"]
        P["AnthropicPlanner.plan()<br/>(history = None)"]
        A --> RW --> P
    end
    RT -- "[{question, ir}]" --> A
    P -- "emit_sql" --> LLM["Anthropic → semantic SQL"]
    LLM --> CMP["parse + validate + compile + execute<br/>(deterministic)"]
    CMP -- "Result(…, rewritten)" --> H
```

## Key technical points

- **Threaded through the repair loop.** `Engine.ask(question, history=None)`
  passes `history` to `planner.plan(...)` on *every* attempt, so a re-plan after a
  recoverable error still has the conversation context.
- **Signature is uniform.** The `Planner` protocol and every stub take
  `history=None`; a planner that ignores it (e.g. the eval stubs) still works.
- **Bounded cost.** Cap of 4 turns × (question + one IR JSON) keeps added tokens
  small and predictable; there is no summarization step.
- **Observability.** With LangSmith on, the `CONVERSATION SO FAR` block appears in
  the nested Anthropic call's `messages` input, so you can watch follow-up
  resolution in the trace. (The parent `Engine.ask` span filters its own inputs to
  just `question` via `_trace_inputs`.)
- **Deliberately not done yet** (earned-abstraction): no cross-session
  persistence (session_state is wiped on restart and when switching datasets) and
  no history summarization. Both are clean follow-ups when needed.

## Tests

`tests/test_memory.py` covers all three layers: `recent_turns` extraction /
error-skipping / cap, `_history_block` rendering, the planner prompt carrying the
block (fake Anthropic client), and `Engine.ask` forwarding history verbatim while
still executing.
