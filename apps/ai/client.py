"""The provider boundary.

One protocol, one adapter per provider, and job code that never names a
vendor. Everything provider-specific - how a JSON schema is expressed, how a
malformed reply surfaces, what a token count is called - belongs to the
adapter. A job receives a validated object or an error and never branches on
which model answered.

Development runs against a local Ollama model. Production has not been chosen,
and nothing here presumes it: adding an adapter is a class, not a refactor.
"""

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from django.conf import settings

from apps.ai.enums import JobType


class LLMError(Exception):
    """The provider could not be reached, or answered with nothing usable."""


@dataclass(frozen=True, slots=True)
class PromptBundle:
    """What a job sends: stable guidance first, volatile payload after.

    The split is not cosmetic. Providers that cache do so on a prefix, so
    anything varying per call has to come after everything that does not, or
    the cache never hits.
    """

    system: str
    user: str
    #: Identifies the guidance this was built from, recorded on the generation.
    prompt_version: str = ""

    def fingerprint(self) -> str:
        """Identical inputs hash alike, which is what makes a repeat visible."""
        return hashlib.sha256(f"{self.system}\x00{self.user}".encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class LLMResult:
    """One call's outcome, provider-shaped detail already flattened."""

    parsed: dict
    raw: str
    provider: str
    model_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    extra: dict = field(default_factory=dict)


class LLMClient(Protocol):
    """What every provider must offer.

    `schema` is a JSON Schema the reply must satisfy. Adapters are expected to
    enforce it at the decoder rather than asking politely in the prompt - that
    is what removes the whole class of "returned prose instead of JSON"
    failures, and it is the main reason this signature takes a schema at all.
    """

    name: str

    def complete(self, *, prompt: PromptBundle, schema: dict, job: str) -> LLMResult: ...


class OllamaClient:
    """A local or self-hosted Ollama server.

    Uses Ollama's structured-output support: passing a JSON Schema as `format`
    constrains generation, so the reply parses or the server errors - it does
    not come back as an apology in prose.
    """

    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.base_url = (base_url or settings.OLLAMA_BASE_URL).rstrip("/")
        self.model = model or settings.OLLAMA_MODEL
        self.timeout = timeout or settings.AI_TIMEOUT_SECONDS

    def complete(self, *, prompt: PromptBundle, schema: dict, job: str) -> LLMResult:
        import httpx

        payload = {
            "model": self.model,
            "stream": False,
            "format": schema,
            "messages": [
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
            # Marking and tagging are judgements, not compositions. Sampling
            # would make the same answer score differently on a re-run, which
            # is not a property a child's diagnosis should have.
            "options": {"temperature": 0},
        }

        started = time.perf_counter()
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(f"{self.base_url}/api/chat", json=payload)
                response.raise_for_status()
                body = response.json()
        except Exception as exc:  # every transport failure reads alike to a caller
            raise LLMError(f"{self.name} call failed for {job}: {exc}") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        raw = (body.get("message") or {}).get("content", "")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMError(f"{self.name} returned unparseable output for {job}") from exc
        if not isinstance(parsed, dict):
            raise LLMError(f"{self.name} returned {type(parsed).__name__}, expected an object")

        return LLMResult(
            parsed=parsed,
            raw=raw,
            provider=self.name,
            model_id=self.model,
            input_tokens=body.get("prompt_eval_count", 0) or 0,
            output_tokens=body.get("eval_count", 0) or 0,
            latency_ms=latency_ms,
        )


class ScriptedClient:
    """Returns canned replies. For tests and for running with no model.

    Not a mock in the test-double sense - it implements the protocol properly,
    so it also serves as the fallback when no provider is configured, letting
    the rest of the system be exercised without a GPU in the room.
    """

    name = "scripted"

    def __init__(self, replies: dict[str, dict] | None = None, model_id: str = "scripted") -> None:
        self.replies = replies or {}
        self.model_id = model_id
        self.calls: list[tuple[str, PromptBundle]] = []

    def complete(self, *, prompt: PromptBundle, schema: dict, job: str) -> LLMResult:  # noqa: ARG002
        self.calls.append((job, prompt))
        if job not in self.replies:
            raise LLMError(f"No scripted reply for {job}")
        parsed = self.replies[job]
        return LLMResult(
            parsed=parsed,
            raw=json.dumps(parsed),
            provider=self.name,
            model_id=self.model_id,
        )


#: Swapped in tests via `override_settings(AI_CLIENT=...)` or by assigning here.
_registry: dict[str, Any] = {}


def register_client(client: Any) -> None:
    """Install a client for the whole process. Used by tests and by startup."""
    _registry["client"] = client


def reset_client() -> None:
    """Forget any installed client. Tests must call this between cases, or one
    case's scripted replies answer the next one's calls."""
    _registry.pop("client", None)


def get_client(job: str | JobType | None = None) -> LLMClient:  # noqa: ARG001
    """The client to use.

    Takes `job` so a future split - a small model for marking, a larger one for
    plans - is a change here rather than at every call site.
    """
    if "client" in _registry:
        return _registry["client"]
    if not getattr(settings, "AI_ENABLED", False):
        return ScriptedClient()
    return OllamaClient()


__all__ = [
    "LLMClient",
    "LLMError",
    "LLMResult",
    "OllamaClient",
    "PromptBundle",
    "ScriptedClient",
    "get_client",
    "register_client",
    "reset_client",
]
