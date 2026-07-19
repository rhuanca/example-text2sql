"""Observability seam: token-usage capture (`usage`) and local persistence
(`store`) for conversations, turns, and LLM calls. Pure Python + stdlib sqlite3;
no framework. The professional layer (LangSmith) is wired separately and stays
optional behind LANGSMITH_TRACING."""
