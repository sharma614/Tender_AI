"""
app/agents/base.py — Shared Gemini client + instrumented invocation for all agents.

Every agent LLM call is timed and token-counted, on both the success and the
failure path, and returns a `ToolCallRecord` alongside its parsed result.

Two deliberate design choices:

1. **Agents never hold a DB Session.** Extraction, summarization and risk run as
   concurrent LangGraph nodes; a shared SQLAlchemy Session across threads would
   corrupt state on concurrent commit. Agents return records; the orchestrator's
   `persist` node writes them single-threaded.
2. **Failures still produce a record.** A raised `AgentError` carries its
   `ToolCallRecord`, so a failed call's latency and error text survive into
   /admin/metrics instead of being swallowed by `except: raise`.
"""
import json
import os
import time
from typing import Any, Dict, Optional, Tuple, Type, TypeVar

import google.generativeai as genai
from pydantic import BaseModel, ValidationError

from ..schemas import ToolCallRecord

T = TypeVar("T", bound=BaseModel)

# Single source of truth for the model id, so an ablation across models is a
# one-line config change rather than an edit to four agent files.
#
# gemini-1.5-flash and gemini-2.5-flash both now return 404 for new API keys
# ("no longer available to new users"); the API's own migration target is
# gemini-3.6-flash, which is what this was verified against.
DEFAULT_MODEL_NAME = os.environ.get("GEMINI_MODEL_NAME", "gemini-3.6-flash")

# The flash models accept ~1M tokens; this bound only guards pathological input.
MAX_PROMPT_CHARS = 1_500_000

_configured = False


def _configure_once() -> None:
    """Configure the google-generativeai client exactly once per process."""
    global _configured
    if _configured:
        return
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print(
            "Warning: GEMINI_API_KEY is not set. Agent LLM calls will fail; "
            "retrieval-only evaluation does not require it."
        )
    genai.configure(api_key=api_key)
    _configured = True


def _read_usage(response: Any) -> Dict[str, Optional[int]]:
    """
    Extract token counts from a Gemini response.

    Read defensively: `usage_metadata` is not guaranteed across provider or SDK
    versions, and a missing counter must degrade to null columns rather than
    crash a pipeline run.

    Measurement caveat that matters when these numbers are reported: on the
    gemini-3.x flash models `total_token_count` is substantially larger than
    `prompt_token_count + candidates_token_count`, because billable reasoning
    ("thinking") tokens are included in the total but are not broken out as a
    separate field by this SDK version. `total_tokens` is therefore the only
    complete figure; treating prompt+completion as the total undercounts badly.
    """
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
    return {
        "prompt_tokens": getattr(usage, "prompt_token_count", None),
        "completion_tokens": getattr(usage, "candidates_token_count", None),
        "total_tokens": getattr(usage, "total_token_count", None),
    }


class AgentError(RuntimeError):
    """
    An agent LLM call or response parse failed.

    Carries the partially-populated `ToolCallRecord` so the caller can persist
    the failure (with its latency) before propagating.
    """

    def __init__(self, message: str, record: ToolCallRecord):
        super().__init__(message)
        self.record = record


class BaseAgent:
    """
    Base for the four tender agents.

    Subclasses set `agent_name` and call `self._invoke(...)` with a prompt, the
    Pydantic model to parse into, and a logical tool name.
    """

    agent_name: str = "BaseAgent"

    def __init__(self, model_name: Optional[str] = None, temperature: float = 0.1):
        _configure_once()
        self.model_name = model_name or DEFAULT_MODEL_NAME
        self.temperature = temperature
        self.model = genai.GenerativeModel(
            model_name=self.model_name,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                temperature=temperature,
            ),
        )

    def _invoke(
        self,
        prompt: str,
        response_model: Type[T],
        tool_name: str,
        input_arguments: Optional[Dict[str, Any]] = None,
    ) -> Tuple[T, ToolCallRecord]:
        """
        Run one instrumented LLM call and parse the JSON response.

        Returns
        -------
        (parsed_result, record)

        Raises
        ------
        AgentError
            On transport failure, malformed JSON, or schema validation failure.
            `err.record` holds the instrumentation for the failed attempt.
        """
        record = ToolCallRecord(
            agent_name=self.agent_name,
            tool_name=tool_name,
            input_arguments=input_arguments or {},
            model_name=self.model_name,
        )

        started = time.perf_counter()
        try:
            response = self.model.generate_content(prompt)
            record.latency_ms = int((time.perf_counter() - started) * 1000)

            usage = _read_usage(response)
            record.prompt_tokens = usage["prompt_tokens"]
            record.completion_tokens = usage["completion_tokens"]
            record.total_tokens = usage["total_tokens"]

            data = json.loads(response.text)
            parsed = response_model(**data)

        except (json.JSONDecodeError, ValidationError) as exc:
            # Latency is already set unless generate_content itself raised.
            if record.latency_ms is None:
                record.latency_ms = int((time.perf_counter() - started) * 1000)
            record.status = "error"
            record.error_message = f"{type(exc).__name__}: {exc}"
            raise AgentError(
                f"{self.agent_name}.{tool_name} returned unparseable output: {exc}", record
            ) from exc

        except Exception as exc:
            record.latency_ms = int((time.perf_counter() - started) * 1000)
            record.status = "error"
            record.error_message = f"{type(exc).__name__}: {exc}"
            raise AgentError(f"{self.agent_name}.{tool_name} failed: {exc}", record) from exc

        record.status = "success"
        record.output_response = data
        return parsed, record

    @staticmethod
    def _bound(text: str) -> str:
        """Clamp document text to a safe prompt size."""
        return text[:MAX_PROMPT_CHARS]
