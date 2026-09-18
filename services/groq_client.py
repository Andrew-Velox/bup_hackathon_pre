# Backwards-compatible export bridging to unified LLM client
from services.llm_client import extract_directives, SYSTEM_PROMPT

__all__ = ["extract_directives", "SYSTEM_PROMPT"]
