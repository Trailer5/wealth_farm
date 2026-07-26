"""OpenAI-compatible Chat Completions adapter with structured JSON output."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Any, Callable

from .models import (
    AnalysisClaim,
    AnalysisTask,
    EvidenceReference,
    ModelOutput,
)


class ModelAdapterError(RuntimeError):
    """Raised when an OpenAI-compatible request or response is invalid."""


@dataclass(frozen=True)
class OpenAICompatibleConfig:
    base_url: str
    model: str
    api_key: str | None = None
    timeout_seconds: float = 60.0

    @classmethod
    def from_env(cls) -> "OpenAICompatibleConfig":
        base_url = os.getenv("WEALTH_FARM_AI_BASE_URL", "").strip()
        model = os.getenv("WEALTH_FARM_AI_MODEL", "").strip()
        api_key = os.getenv("WEALTH_FARM_AI_API_KEY", "").strip() or None
        if not base_url:
            raise ValueError("WEALTH_FARM_AI_BASE_URL is required")
        if not model:
            raise ValueError("WEALTH_FARM_AI_MODEL is required")
        return cls(
            base_url=base_url,
            model=model,
            api_key=api_key,
        )


class OpenAICompatibleAdapter:
    """Send one evidence-backed task to a compatible chat endpoint."""

    def __init__(
        self,
        config: OpenAICompatibleConfig,
        post_function: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        if config.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.config = config
        self.post_function = post_function or _post_json
        self.name = f"openai-compatible:{config.model}"

    def generate(
        self,
        task: AnalysisTask,
        evidence: tuple[EvidenceReference, ...],
    ) -> ModelOutput:
        """Request and parse a strict structured analysis response."""

        headers = {"Content-Type": "application/json"}
        if self.config.api_key is not None:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        payload = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "system",
                    "content": _SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": asdict(task),
                            "evidence": [
                                asdict(item)
                                for item in evidence
                            ],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
        }
        try:
            response = self.post_function(
                _chat_completions_url(self.config.base_url),
                headers=headers,
                payload=payload,
                timeout_seconds=self.config.timeout_seconds,
            )
        except Exception as exc:
            raise ModelAdapterError(
                f"OpenAI-compatible request failed: {type(exc).__name__}: {exc}"
            ) from exc
        return _parse_response(response)


def _chat_completions_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/chat/completions"


def _post_json(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    import requests

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    return response.json()


def _parse_response(response: dict[str, Any]) -> ModelOutput:
    try:
        raw_content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelAdapterError("Response does not contain message content") from exc
    if not isinstance(raw_content, str):
        raise ModelAdapterError("Message content must be a JSON string")
    try:
        payload = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        raise ModelAdapterError("Message content is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ModelAdapterError("Structured output must be a JSON object")
    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ModelAdapterError("Structured output requires non-empty content")
    data = payload.get("data") or {}
    citations = payload.get("citations") or []
    claims_payload = payload.get("claims") or []
    if not isinstance(data, dict):
        raise ModelAdapterError("data must be an object")
    if not isinstance(citations, list) or not all(
        isinstance(value, str)
        for value in citations
    ):
        raise ModelAdapterError("citations must be a string array")
    if not isinstance(claims_payload, list):
        raise ModelAdapterError("claims must be an array")
    claims = []
    for item in claims_payload:
        if not isinstance(item, dict):
            raise ModelAdapterError("claim must be an object")
        claim_type = item.get("claim_type")
        text = item.get("text")
        evidence_ids = item.get("evidence_ids") or []
        if not isinstance(claim_type, str) or not isinstance(text, str):
            raise ModelAdapterError("claim requires claim_type and text")
        if not isinstance(evidence_ids, list) or not all(
            isinstance(value, str)
            for value in evidence_ids
        ):
            raise ModelAdapterError("claim evidence_ids must be a string array")
        claims.append(
            AnalysisClaim(
                claim_type=claim_type,
                text=text,
                evidence_ids=tuple(evidence_ids),
            )
        )
    return ModelOutput(
        content=content,
        data=data,
        citations=tuple(citations),
        claims=tuple(claims),
    )


_SYSTEM_PROMPT = """
Return exactly one JSON object with:
- content: non-empty natural language analysis
- data: object with optional structured fields
- citations: array of evidence_id strings
- claims: array of objects containing claim_type, text, evidence_ids

claim_type must be fact, inference, or suggestion.
Facts and inferences must cite evidence IDs supplied by the user.
Do not invent evidence IDs or facts absent from the evidence snapshot.
""".strip()
