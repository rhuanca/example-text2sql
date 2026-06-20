"""Engine orchestration: plan -> compile -> validate -> execute, with a bounded
repair loop that re-plans with the prior error appended."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..semantic.model import SemanticModel
from .compiler import CompileError, compile
from .dialects.base import Dialect
from .ir import SemanticQuery
from .planner import Planner
from .validator import ValidationError, validate_ir, validate_sql

_RECOVERABLE = (ValidationError, CompileError, KeyError, sqlite3.Error)


@dataclass
class Result:
    question: str
    ir: SemanticQuery
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

    def ask(self, question: str) -> Result:
        error: str | None = None
        last_exc: Exception | None = None
        for _ in range(self.max_retries + 1):
            ir = self.planner.plan(question, self.model, error=error)
            try:
                validate_ir(ir, self.model)
                sql, params = compile(ir, self.model, self.dialect)
                validate_sql(sql)
                columns, rows = self.executor.run(sql, params)
                return Result(question, ir, sql, params, columns, rows)
            except _RECOVERABLE as e:
                last_exc = e
                error = f"{type(e).__name__}: {e}"
        raise EngineError(
            f"could not answer after {self.max_retries + 1} attempts: {error}"
        ) from last_exc
