"""Engine orchestration: plan -> compile -> validate -> execute, with a bounded
repair loop that re-plans with the prior error appended."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..semantic.model import SemanticModel
from .compare import Comparison, compile_comparison, validate_comparison
from .compiler import CompileError, compile
from .dialects.base import Dialect
from .ir import SemanticQuery
from .planner import Planner
from .validator import ValidationError, validate_ir, validate_sql

try:  # LangSmith is optional; a no-op passthrough when it isn't installed.
    from langsmith import traceable
except ImportError:

    def traceable(*args, **kwargs):
        def decorator(fn):
            return fn

        return args[0] if args and callable(args[0]) else decorator


_RECOVERABLE = (ValidationError, CompileError, KeyError, sqlite3.Error)


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
    ir: SemanticQuery | Comparison  # the plan: a normal query or a period comparison
    sql: str
    params: list
    columns: list
    rows: list


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
    ):
        self.model = model
        self.planner = planner
        self.dialect = dialect
        self.executor = executor
        self.max_retries = max_retries

    @traceable(
        run_type="chain",
        name="Engine.ask",
        process_inputs=_trace_inputs,
        process_outputs=_trace_outputs,
    )
    def ask(self, question: str, history: list | None = None) -> Result:
        error: str | None = None
        last_exc: Exception | None = None
        for _ in range(self.max_retries + 1):
            plan = self.planner.plan(
                question, self.model, error=error, history=history
            )
            try:
                if isinstance(plan, Comparison):
                    validate_comparison(plan, self.model)
                    sql, params = compile_comparison(plan, self.model, self.dialect)
                else:
                    validate_ir(plan, self.model)
                    sql, params = compile(plan, self.model, self.dialect)
                validate_sql(sql)
                columns, rows = self.executor.run(sql, params)
                return Result(question, plan, sql, params, columns, rows)
            except _RECOVERABLE as e:
                last_exc = e
                error = f"{type(e).__name__}: {e}"
        raise EngineError(
            f"could not answer after {self.max_retries + 1} attempts: {error}"
        ) from last_exc
