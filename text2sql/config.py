"""Runtime configuration. Loads a local .env (if present) so the Anthropic key
and model can live outside the code. Reading env vars at runtime is fine; the
.env file itself is gitignored and never committed."""

from __future__ import annotations

import os

from dotenv import load_dotenv

# Load .env from the nearest parent directory, without overriding real env vars.
load_dotenv(override=True)

DEFAULT_MODEL = "claude-opus-4-8"


def get_api_key() -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    return key or None


def get_model() -> str:
    return os.environ.get("ANTHROPIC_MODEL") or DEFAULT_MODEL
