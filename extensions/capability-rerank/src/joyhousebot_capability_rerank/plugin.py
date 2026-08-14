"""Provider-neutral reranking over candidates already authorized by Runtime."""

from __future__ import annotations

import re
from typing import Any

from joyhousebot.extension_sdk import (
    CapabilityContext,
    CapabilityDefinition,
    CapabilityKind,
    CapabilityRef,
    CapabilityResult,
    PluginManifest,
)
from joyhousebot.extension_sdk.manifest import source_tree_digest

_WORD = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_CJK = re.compile(r"[\u3400-\u9fff]")
_MAX_CANDIDATES = 50
_MAX_TEXT_LENGTH = 20_000


def _terms(value: str) -> set[str]:
    """Return deterministic lexical terms including CJK unigrams/bigrams."""
    text = value.casefold()
    terms = set(_WORD.findall(text))
    chars = _CJK.findall(text)
    terms.update(chars)
    terms.update("".join(chars[index : index + 2]) for index in range(len(chars) - 1))
    return terms


def _score(query: str, candidate: str) -> float:
    query_terms = _terms(query)
    candidate_terms = _terms(candidate)
    if not query_terms or not candidate_terms:
        return 0.0
    overlap = len(query_terms & candidate_terms)
    coverage = overlap / len(query_terms)
    precision = overlap / len(candidate_terms)
    phrase_bonus = 0.1 if query.casefold() in candidate.casefold() else 0.0
    return round((0.8 * coverage) + (0.2 * precision) + phrase_bonus, 6)


def _failure(code: str, message: str) -> CapabilityResult:
    return CapabilityResult(success=False, error={"code": code, "message": message, "retryable": False})


class RerankHandler:
    async def execute(
        self, context: CapabilityContext, input: dict[str, Any]
    ) -> CapabilityResult:
        query = str(input.get("query") or "").strip()
        candidates = list(input.get("candidates") or [])
        top_k = int(input.get("top_k") or len(candidates))
        if not query:
            return _failure("INVALID_PARAMETERS", "query is required")
        if not candidates or len(candidates) > _MAX_CANDIDATES:
            return _failure("INVALID_PARAMETERS", "candidates must contain 1-50 items")
        if not 1 <= top_k <= min(len(candidates), _MAX_CANDIDATES):
            return _failure("INVALID_PARAMETERS", "top_k must not exceed candidate count")

        prepared: list[tuple[str, float]] = []
        seen: set[str] = set()
        for item in candidates:
            candidate = dict(item or {})
            candidate_id = str(candidate.get("candidate_id") or "").strip()
            text = str(candidate.get("text") or "")
            if not candidate_id or candidate_id in seen or len(text) > _MAX_TEXT_LENGTH:
                return _failure(
                    "INVALID_PARAMETERS",
                    "candidate ids must be unique and candidate text must be <= 20000 characters",
                )
            seen.add(candidate_id)
            prepared.append((candidate_id, _score(query, text)))
        prepared.sort(key=lambda item: (-item[1], item[0]))
        ranked = [
            {"candidate_id": candidate_id, "score": score, "rank": index + 1}
            for index, (candidate_id, score) in enumerate(prepared[:top_k])
        ]
        return CapabilityResult(
            success=True,
            output={
                "ranked": ranked,
                "model": {"provider": "local", "model": "lexical-v1", "version": "0.1.0"},
                "fallback": False,
                "candidate_count": len(candidates),
            },
        )


_CANDIDATE = {
    "type": "object",
    "required": ["candidate_id", "text"],
    "properties": {
        "candidate_id": {"type": "string", "minLength": 1, "maxLength": 256},
        "text": {"type": "string", "maxLength": _MAX_TEXT_LENGTH},
    },
    "additionalProperties": False,
}

_SCHEMA = {
    "type": "object",
    "required": ["query", "candidates"],
    "properties": {
        "query": {"type": "string", "minLength": 1, "maxLength": 20_000},
        "candidates": {"type": "array", "minItems": 1, "maxItems": _MAX_CANDIDATES, "items": _CANDIDATE},
        "top_k": {"type": "integer", "minimum": 1, "maximum": _MAX_CANDIDATES},
    },
    "additionalProperties": False,
}


class RerankCapabilityPlugin:
    plugin_id = "capability-rerank"
    version = "0.1.0"

    def manifest(self) -> PluginManifest:
        return PluginManifest(
            plugin_id=self.plugin_id,
            version=self.version,
            name="Scoped Candidate Rerank",
            description="Locally reorder already-authorized retrieval candidates.",
            distribution_name="joyhousebot-capability-rerank",
            build_digest=source_tree_digest(__file__),
            runtime_contract_version=2,
            required_permissions=("context.read",),
        )

    def register(self, registry: Any) -> None:
        registry.register_capability(
            CapabilityDefinition(
                ref=CapabilityRef("retrieval.rerank", self.version, CapabilityKind.TOOL),
                name="Rerank retrieval candidates",
                description="Reorder already-authorized candidate text; never performs retrieval.",
                input_schema=_SCHEMA,
                output_schema={"type": "object"},
                adapter="plugin",
                tags=("retrieval", "rerank", "local"),
                expected_duration_seconds=1,
                timeout_seconds=10,
                idempotent=True,
                retryable=False,
                side_effect="read",
                permissions=("context.read",),
                data_classification="confidential",
            ),
            RerankHandler(),
        )

    def health_checks(self) -> tuple[Any, ...]:
        return ()


def create_plugin() -> RerankCapabilityPlugin:
    return RerankCapabilityPlugin()
