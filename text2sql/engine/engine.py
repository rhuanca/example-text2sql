"""Engine orchestration: plan -> compile -> validate -> execute, with a bounded
repair loop that re-plans with the prior error appended."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..semantic.model import SemanticModel
from .compare import Comparison, compile_comparison, validate_comparison
from .compiler import CompileError, compile as compile_ir, resolve_window_sql
from .dialects.base import Dialect
from .ir import SemanticQuery
from .planner import Planner
from .semantic_sql import QueryShape, SemanticSqlError, compile_semantic_sql
from .validator import ValidationError, validate_ir, validate_sql

try:  # LangSmith is optional; a no-op passthrough when it isn't installed.
    from langsmith import traceable
except ImportError:

    def traceable(*args, **kwargs):
        def decorator(fn):
            return fn

        return args[0] if args and callable(args[0]) else decorator


_RECOVERABLE = (SemanticSqlError, ValidationError, CompileError, KeyError, sqlite3.Error)


def _tag_thread(thread_id: str | None) -> None:
    """Attach the conversation id to the current LangSmith run so all turns in a
    session group into one thread. Best-effort: a no-op when LangSmith is absent
    or tracing is off (there's no active run tree)."""
    if not thread_id:
        return
    try:
        from langsmith.run_helpers import get_current_run_tree

        rt = get_current_run_tree()
        if rt is not None:
            rt.add_metadata({"thread_id": thread_id, "session_id": thread_id})
    except Exception:
        pass


def _trace_inputs(inputs: dict) -> dict:
    # Only the question is interesting; drop `self` (the Engine) from the trace.
    return {"question": inputs.get("question")}


def _trace_outputs(result: "Result") -> dict:
    # Summarize the pipeline output: the plan the LLM chose, the compiled SQL and
    # bound params, the columns, and the row count (not every row).
    return {
        "plan": result.ir.to_dict(),
        "sql": result.sql,
        "params": result.params,
        "columns": result.columns,
        "row_count": len(result.rows),
    }


@dataclass
class Result:
    question: str
    ir: "SemanticQuery | Comparison | QueryShape"  # plan / output shape carried for charts
    sql: str  # the compiled physical SQL that ran
    params: list
    columns: list
    rows: list
    semantic_sql: str | None = None  # the LLM-authored semantic SQL, if any
    rewritten: str | None = None  # the standalone question, if a rewrite carried scope
    # A relative time window ("past month") resolved to its concrete bucket(s), so the
    # UI can say *which* period — first and last bucket (equal for a single-unit window).
    period_start: str | None = None
    period_end: str | None = None


class EngineError(Exception):
    pass


class Engine:
    def __init__(
        self,
        model: SemanticModel,
        planner: Planner,
        dialect: Dialect,
        executor,
        max_retries: int = 1,
        rewriter=None,
    ):
        self.model = model
        self.planner = planner
        self.dialect = dialect
        self.executor = executor
        self.max_retries = max_retries
        self.rewriter = rewriter

    @traceable(
        run_type="chain",
        name="Engine.ask",
        process_inputs=_trace_inputs,
        process_outputs=_trace_outputs,
    )
    def ask(self, question: str, history: list | None = None,
            thread_id: str | None = None) -> Result:
        _tag_thread(thread_id)  # group this turn under its conversation in LangSmith
        # Conversational scope carries here: a rewriter decontextualizes the
        # follow-up into a standalone question, so the planner plans that instead
        # (with no history block — the question already stands alone). Without a
        # rewriter, fall back to threading `history` into the planner's prompt.
        used_rewrite = bool(self.rewriter and history)
        asked = self.rewriter.rewrite(question, history) if used_rewrite else question
        hist = None if used_rewrite else history
        rewritten = asked if (used_rewrite and asked.strip() != question.strip()) else None

        error: str | None = None
        last_exc: Exception | None = None
        for _ in range(self.max_retries + 1):
            plan = self.planner.plan(
                asked, self.model, error=error, history=hist
            )
            try:
                # The real planner returns semantic SQL (a str): parse + validate +
                # compile it (a plain query, a CASE-pivot Comparison, or a window
                # query). Test stubs may return a plan object directly.
                if isinstance(plan, str):
                    semantic_sql = plan
                    sql, params, ir = compile_semantic_sql(plan, self.model, self.dialect)
                else:
                    semantic_sql, ir = None, plan
                    if isinstance(plan, Comparison):
                        validate_comparison(plan, self.model)
                        sql, params = compile_comparison(plan, self.model, self.dialect)
                    else:
                        validate_ir(plan, self.model)
                        sql, params = compile_ir(plan, self.model, self.dialect)
                validate_sql(sql)
                columns, rows = self.executor.run(sql, params)
                period_start, period_end = self._resolve_period(ir)
                return Result(question, ir, sql, params, columns, rows,
                              semantic_sql, rewritten=rewritten,
                              period_start=period_start, period_end=period_end)
            except _RECOVERABLE as e:
                last_exc = e
                error = f"{type(e).__name__}: {e}"
        raise EngineError(
            f"could not answer after {self.max_retries + 1} attempts: {error}"
        ) from last_exc

    def _resolve_period(self, ir) -> tuple[str | None, str | None]:
        """Resolve a plan's relative time window to its concrete bucket boundaries so
        the UI can report which period was covered. Best-effort: any failure just
        yields (None, None) and never affects the answer."""
        t = getattr(ir, "time", None)
        if t is None:
            return None, None
        try:
            _, rows = self.executor.run(resolve_window_sql(t, self.dialect), [])
            if rows:
                return rows[0][0], rows[0][1]
        except Exception:
            pass
        return None, None
