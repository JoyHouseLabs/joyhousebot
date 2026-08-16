"""Execute versioned Eval cases against exact runtime targets."""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any

from porthouse.application.errors import ConflictError, NotFoundError, ValidationError
from porthouse.application.evals import stable_observation_hash
from porthouse.domain.capabilities.models import CapabilityRef
from porthouse.domain.prompts import render_prompt_document
from porthouse.runtime.models import AgentOptions, GraphTaskSpec, TaskGraphSpec
from porthouse.services.retrieval.knowledge_repository import KnowledgeRepository

_TERMINAL = {"completed", "failed", "cancelled", "timed_out"}


class EvalExecutionService:
    """Durable, resumable execution adapter for Eval datasets."""

    def __init__(self, *, store: Any, runtime: Any, evals: Any, scenarios: Any) -> None:
        self.store = store
        self.runtime = runtime
        self.evals = evals
        self.scenarios = scenarios

    async def enqueue(
        self,
        eval_run_id: str,
        *,
        actor_id: str,
        max_concurrency: int = 4,
        case_timeout_seconds: float = 300.0,
    ) -> dict[str, Any]:
        """Queue execution durably; a Scheduler Worker owns orchestration."""
        try:
            return await asyncio.to_thread(
                self.store.enqueue_eval_execution,
                eval_run_id,
                configuration={
                    "max_concurrency": max(1, min(int(max_concurrency), 16)),
                    "case_timeout_seconds": max(
                        1.0, min(float(case_timeout_seconds), 3600.0)
                    ),
                },
                requested_by=actor_id,
            )
        except ValueError as exc:
            if "not found" in str(exc):
                raise NotFoundError(str(exc)) from exc
            raise ConflictError(str(exc)) from exc

    async def execute(
        self,
        eval_run_id: str,
        *,
        actor_id: str,
        max_concurrency: int = 4,
        case_timeout_seconds: float = 300.0,
    ) -> dict[str, Any]:
        run = await asyncio.to_thread(self.store.get_eval_run, eval_run_id)
        if run is None:
            raise NotFoundError("evaluation run not found")
        if run["status"] != "running":
            return run
        suite = await asyncio.to_thread(
            self.store.get_eval_suite, run["suite_id"], run["suite_version"]
        )
        if suite is None:
            raise NotFoundError("evaluation suite not found")
        observed = {str(item["case_id"]) for item in run.get("results") or []}
        pending = [case for case in suite["cases"] if case["case_id"] not in observed]
        semaphore = asyncio.Semaphore(max(1, min(int(max_concurrency), 16)))

        async def execute_case(case: dict[str, Any]) -> None:
            async with semaphore:
                await self._execute_case(
                    run,
                    case,
                    actor_id=actor_id,
                    timeout_seconds=case_timeout_seconds,
                )

        await asyncio.gather(*(execute_case(case) for case in pending))
        return await self.evals.finalize_run(eval_run_id)

    async def _execute_case(
        self,
        eval_run: dict[str, Any],
        case: dict[str, Any],
        *,
        actor_id: str,
        timeout_seconds: float,
    ) -> None:
        started = time.monotonic()
        status = "failed"
        source_run_id: str | None = None
        try:
            output, status, source_run_id = await self._execute_target(
                eval_run,
                case,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - failures are scored evidence
            output = {
                "error": {
                    "type": type(exc).__name__,
                    "message": str(exc)[:1000],
                }
            }
        latency_ms = (time.monotonic() - started) * 1000
        cost_usd = self._cost(output)
        await self.evals.record_observation(
            eval_run["eval_run_id"],
            {
                "case_id": case["case_id"],
                "output": output,
                "status": status,
                "latency_ms": latency_ms,
                "cost_usd": cost_usd,
                "metadata": {
                    "actor_id": actor_id,
                    "source_run_id": source_run_id,
                    "execution_mode": "automated",
                    "observation_sha256": stable_observation_hash(output),
                },
            },
        )

    async def _execute_target(
        self,
        eval_run: dict[str, Any],
        case: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> tuple[dict[str, Any], str, str | None]:
        target_type = str(eval_run["target_type"])
        if target_type == "agent":
            return await self._execute_agent(eval_run, case, timeout_seconds=timeout_seconds)
        if target_type == "prompt":
            return await self._execute_prompt(eval_run, case, timeout_seconds=timeout_seconds)
        if target_type == "skill":
            return await self._execute_skill(eval_run, case, timeout_seconds=timeout_seconds)
        if target_type == "scenario":
            return await self._execute_scenario(eval_run, case)
        if target_type == "capability":
            return await self._execute_capability(
                eval_run, case, timeout_seconds=timeout_seconds
            )
        if target_type == "embedding_profile":
            return await self._execute_embedding_profile(
                eval_run, case, timeout_seconds=timeout_seconds
            )
        raise ValidationError(f"unsupported evaluation target: {target_type}")

    async def _execute_prompt(
        self,
        eval_run: dict[str, Any],
        case: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> tuple[dict[str, Any], str, str]:
        prompt_revision = await asyncio.to_thread(
            self.store.get_prompt_revision, str(eval_run["target_revision_id"])
        )
        if prompt_revision is None or prompt_revision["prompt_id"] != str(eval_run["target_id"]):
            raise ConflictError("exact Prompt revision is unavailable")
        values = dict(case.get("input") or {})
        instruction = render_prompt_document(
            prompt_revision, dict(values.get("variables") or {})
        )
        return await self._execute_instruction_asset(
            eval_run,
            case,
            instruction=instruction,
            instruction_ref={
                "prompt_id": prompt_revision["prompt_id"],
                "revision_id": prompt_revision["revision_id"],
                "content_sha256": prompt_revision["content_sha256"],
            },
            timeout_seconds=timeout_seconds,
        )

    async def _execute_skill(
        self,
        eval_run: dict[str, Any],
        case: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> tuple[dict[str, Any], str, str]:
        skill = await asyncio.to_thread(
            self.store.get_skill_version,
            str(eval_run["target_id"]),
            str(eval_run["target_revision_id"]),
        )
        if skill is None:
            raise ConflictError("exact Skill version is unavailable")
        return await self._execute_instruction_asset(
            eval_run,
            case,
            instruction=str(skill["instruction_content"]),
            instruction_ref={
                "skill_id": skill["skill_id"],
                "version": skill["version"],
                "content_sha256": skill["content_sha256"],
            },
            timeout_seconds=timeout_seconds,
        )

    async def _execute_instruction_asset(
        self,
        eval_run: dict[str, Any],
        case: dict[str, Any],
        *,
        instruction: str,
        instruction_ref: dict[str, str],
        timeout_seconds: float,
    ) -> tuple[dict[str, Any], str, str]:
        """Evaluate a Prompt/Skill with an explicitly selected executor Agent."""
        values = dict(case.get("input") or {})
        prompt = str(values.get("prompt") or "").strip()
        if not prompt:
            raise ValidationError("instruction asset evaluation case requires input.prompt")
        agent_id = str(values.get("agent_id") or "default")
        revision_id = values.get("agent_revision_id")
        bounded_timeout = min(timeout_seconds, float(values.get("timeout_seconds") or timeout_seconds))
        record = await self.runtime.submit_run(
            AgentOptions(
                prompt=prompt,
                user_id=f"eval:{eval_run['eval_run_id']}",
                session_id=f"eval:{eval_run['eval_run_id']}:{case['case_id']}",
                agent_id=agent_id,
                agent_revision_id=str(revision_id) if revision_id else None,
                model=values.get("model"),
                system_prompt=instruction,
                output_schema=(
                    dict(values["output_schema"]) if values.get("output_schema") else None
                ),
                timeout_seconds=bounded_timeout,
                metadata={
                    "eval_run_id": eval_run["eval_run_id"],
                    "eval_case_id": case["case_id"],
                    "evaluation": True,
                    "instruction_asset": instruction_ref,
                },
                idempotency_key=f"eval:{eval_run['eval_run_id']}:{case['case_id']}",
            )
        )
        final = await self.runtime.wait(record.run_id, timeout=bounded_timeout + 5)
        if final is None or final.status not in _TERMINAL:
            await self.runtime.cancel(record.run_id, reason="evaluation case timeout")
            final = await self.runtime.wait(record.run_id, timeout=5)
        return await self._runtime_evidence(final or record)

    async def _execute_agent(
        self,
        eval_run: dict[str, Any],
        case: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> tuple[dict[str, Any], str, str]:
        values = dict(case.get("input") or {})
        prompt = str(values.get("prompt") or "").strip()
        if not prompt:
            raise ValidationError("Agent evaluation case requires input.prompt")
        bounded_timeout = min(timeout_seconds, float(values.get("timeout_seconds") or timeout_seconds))
        record = await self.runtime.submit_run(
            AgentOptions(
                prompt=prompt,
                user_id=f"eval:{eval_run['eval_run_id']}",
                session_id=f"eval:{eval_run['eval_run_id']}:{case['case_id']}",
                agent_id=str(eval_run["target_id"]),
                agent_revision_id=str(eval_run["target_revision_id"]),
                model=values.get("model"),
                system_prompt=values.get("system_prompt"),
                output_schema=(
                    dict(values["output_schema"]) if values.get("output_schema") else None
                ),
                verification_policy=dict(values.get("verification_policy") or {}),
                timeout_seconds=bounded_timeout,
                max_turns=(int(values["max_turns"]) if values.get("max_turns") else None),
                max_input_tokens=(
                    int(values["max_input_tokens"])
                    if values.get("max_input_tokens")
                    else None
                ),
                max_output_tokens=(
                    int(values["max_output_tokens"])
                    if values.get("max_output_tokens")
                    else None
                ),
                max_cost_usd=(
                    float(values["max_cost_usd"]) if values.get("max_cost_usd") else None
                ),
                metadata={
                    "eval_run_id": eval_run["eval_run_id"],
                    "eval_case_id": case["case_id"],
                    "evaluation": True,
                },
                idempotency_key=f"eval:{eval_run['eval_run_id']}:{case['case_id']}",
            )
        )
        final = await self.runtime.wait(record.run_id, timeout=bounded_timeout + 5)
        if final is None or final.status not in _TERMINAL:
            await self.runtime.cancel(record.run_id, reason="evaluation case timeout")
            final = await self.runtime.wait(record.run_id, timeout=5)
        return await self._runtime_evidence(final or record)

    async def _execute_scenario(
        self, eval_run: dict[str, Any], case: dict[str, Any]
    ) -> tuple[dict[str, Any], str, None]:
        values = dict(case.get("input") or {})
        prompt = str(values.get("prompt") or "").strip()
        if not prompt:
            raise ValidationError("Scenario evaluation case requires input.prompt")
        try:
            version = int(eval_run["target_revision_id"])
        except ValueError as exc:
            raise ValidationError("Scenario target revision must be an integer") from exc
        output = await self.scenarios.simulate(
            str(eval_run["target_id"]),
            prompt=prompt,
            inputs=dict(values.get("inputs") or {}),
            version=version,
        )
        return output, "completed", None

    async def _execute_capability(
        self,
        eval_run: dict[str, Any],
        case: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> tuple[dict[str, Any], str, str]:
        definition = await asyncio.to_thread(
            self.store.get_capability_definition,
            str(eval_run["target_id"]),
            str(eval_run["target_revision_id"]),
        )
        if definition is None:
            raise ConflictError("capability must be published before automated regression Eval")
        ref = CapabilityRef.from_dict(dict(definition["ref"]))
        profile = await asyncio.to_thread(self.store.get_agent_profile)
        if profile is None:
            raise ConflictError("default Agent is unavailable")
        spec = TaskGraphSpec(
            goal=f"Evaluate capability {ref.capability_id}",
            user_id=f"eval:{eval_run['eval_run_id']}",
            session_id=f"eval:{eval_run['eval_run_id']}:{case['case_id']}",
            agent_id=profile.definition.agent_id,
            max_concurrent=1,
            fail_fast=True,
            aggregate=True,
            idempotency_key=f"eval:{eval_run['eval_run_id']}:{case['case_id']}",
            metadata={"eval_run_id": eval_run["eval_run_id"], "evaluation": True},
            tasks=[
                GraphTaskSpec(
                    id="capability",
                    prompt=f"Evaluate {ref.capability_id}",
                    capability=ref,
                    capability_input=dict(case.get("input") or {}).get("arguments") or {},
                    timeout_seconds=timeout_seconds,
                    node_type="capability",
                )
            ],
        )
        record = await self.runtime.submit_graph(spec)
        final = await self.runtime.wait(record.run_id, timeout=timeout_seconds + 5)
        if final is None or final.status not in _TERMINAL:
            await self.runtime.cancel(record.run_id, reason="evaluation case timeout")
            final = await self.runtime.wait(record.run_id, timeout=5)
        return await self._runtime_evidence(final or record)

    async def _execute_embedding_profile(
        self,
        eval_run: dict[str, Any],
        case: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> tuple[dict[str, Any], str, str]:
        profile = await asyncio.to_thread(
            self.store.get_embedding_profile_execution_revision,
            str(eval_run["target_revision_id"]),
            allow_draft_evaluation=True,
        )
        if profile is None or profile["profile_id"] != str(eval_run["target_id"]):
            raise ConflictError("exact embedding Profile revision is unavailable")
        definitions = {
            str(dict(item.get("ref") or {}).get("capability_id") or ""): item
            for item in await asyncio.to_thread(self.store.list_capability_definitions)
        }
        if "retrieve" not in definitions or "knowledge.index" not in definitions:
            raise ConflictError(
                "published retrieve and knowledge.index capabilities are required "
                "for retrieval Eval"
            )
        retrieve = CapabilityRef.from_dict(dict(definitions["retrieve"]["ref"]))
        index = CapabilityRef.from_dict(dict(definitions["knowledge.index"]["ref"]))
        runtime_profile = await asyncio.to_thread(self.store.get_agent_profile)
        if runtime_profile is None:
            raise ConflictError("default Agent is unavailable")
        values = dict(case.get("input") or {})
        arguments = dict(values.get("arguments") or values)
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ValidationError("retrieval Eval case requires input.query")
        corpus = list(values.get("corpus") or [])
        if not corpus or len(corpus) > 100:
            raise ValidationError("retrieval Eval case requires 1-100 corpus documents")
        eval_user_id = f"eval:{eval_run['eval_run_id']}"
        tasks: list[GraphTaskSpec] = []
        dependencies: list[str] = []
        for position, raw_document in enumerate(corpus):
            if not isinstance(raw_document, dict):
                raise ValidationError("retrieval Eval corpus documents must be objects")
            source_id = str(raw_document.get("source_id") or f"document-{position + 1}")
            eval_source_id = f"{case['case_id']}:{source_id}"
            title = str(raw_document.get("title") or source_id).strip()
            content = str(raw_document.get("content") or "")
            if not title or not content or len(content.encode("utf-8")) > 1_000_000:
                raise ValidationError(
                    "retrieval Eval corpus documents require bounded title and content"
                )
            task_id = f"index-{position + 1}"
            dependencies.append(task_id)
            tasks.append(
                GraphTaskSpec(
                    id=task_id,
                    prompt="Index immutable retrieval Eval corpus",
                    capability=index,
                    capability_input={
                        "source_system": "eval",
                        "source_id": eval_source_id,
                        "source_version": "1",
                        "source_generation": 1,
                        "source_status": "active",
                        "source_type": "note",
                        "title": title,
                        "content": content,
                        "source_url": "",
                        "attachments": [],
                        "tags": ["retrieval-eval"],
                        "collection_refs": [],
                        "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                        "index_profile_id": "semantic-text-v1",
                        "embedding_profile_id": profile["revision_id"],
                    },
                    timeout_seconds=timeout_seconds,
                    node_type="capability",
                    metadata={
                        "evaluation": True,
                        "eval_run_id": eval_run["eval_run_id"],
                        "eval_case_id": case["case_id"],
                        "embedding_profile_id": profile["revision_id"],
                    },
                )
            )
        tasks.append(
            GraphTaskSpec(
                id="retrieve",
                prompt="Evaluate governed private Knowledge retrieval",
                dependencies=dependencies,
                capability=retrieve,
                capability_input={
                    **arguments,
                    "query": query,
                    "scope": "knowledge",
                },
                timeout_seconds=timeout_seconds,
                node_type="capability",
                metadata={
                    "evaluation": True,
                    "eval_run_id": eval_run["eval_run_id"],
                    "eval_case_id": case["case_id"],
                    "embedding_profile_id": profile["revision_id"],
                },
            )
        )
        spec = TaskGraphSpec(
            goal=f"Evaluate Knowledge retrieval with {profile['revision_id']}",
            user_id=eval_user_id,
            session_id=f"eval:{eval_run['eval_run_id']}:{case['case_id']}",
            agent_id=runtime_profile.definition.agent_id,
            max_concurrent=1,
            fail_fast=True,
            aggregate=False,
            idempotency_key=f"eval:{eval_run['eval_run_id']}:{case['case_id']}",
            authority_permissions=["context.read", "knowledge.write"],
            metadata={
                "eval_run_id": eval_run["eval_run_id"],
                "evaluation": True,
                "embedding_profile_id": profile["revision_id"],
            },
            tasks=tasks,
        )
        repository = getattr(self.store, "_knowledge_repository", None)
        if repository is None:
            repository = KnowledgeRepository(self.store)
            self.store._knowledge_repository = repository
        try:
            record = await self.runtime.submit_graph(spec)
            final = await self.runtime.wait(record.run_id, timeout=timeout_seconds + 5)
            if final is None or final.status not in _TERMINAL:
                await self.runtime.cancel(record.run_id, reason="retrieval Eval case timeout")
                final = await self.runtime.wait(record.run_id, timeout=5)
            evidence, status, source_run_id = await self._runtime_evidence(final or record)
            tasks = list(evidence.get("tasks") or [])
            evidence["retrieval"] = next(
                (
                    dict(item.get("result") or {}).get("capability_result")
                    for item in tasks
                    if str(item.get("task_id") or "").endswith(":retrieve")
                ),
                None,
            )
            cost = await asyncio.to_thread(
                repository.embedding_eval_cost,
                eval_run_id=eval_run["eval_run_id"],
                eval_case_id=case["case_id"],
            )
            evidence["usage"] = {"cost_usd": cost}
            return evidence, status, source_run_id
        finally:
            # Eval corpus is evidence input, not user Knowledge. Remove the
            # synthetic namespace after scoring output is frozen in memory.
            for position, raw_document in enumerate(corpus):
                source_id = str(
                    raw_document.get("source_id") or f"document-{position + 1}"
                )
                eval_source_id = f"{case['case_id']}:{source_id}"
                document = await asyncio.to_thread(
                    repository.get_document_by_source,
                    user_id=eval_user_id,
                    source_system="eval",
                    source_id=eval_source_id,
                )
                if document is not None:
                    await asyncio.to_thread(
                        repository.delete_document,
                        user_id=eval_user_id,
                        doc_id=document["doc_id"],
                        actor_id="eval-cleanup",
                    )

    async def _runtime_evidence(self, record: Any) -> tuple[dict[str, Any], str, str]:
        events, artifacts, verifications = await asyncio.gather(
            asyncio.to_thread(self.store.list_runtime_events, record.run_id),
            asyncio.to_thread(
                self.store.list_runtime_artifacts,
                record.run_id,
                user_id=record.user_id,
            ),
            asyncio.to_thread(self.store.list_verification_records, record.run_id),
        )
        evidence = {
            "runtime_run_id": record.run_id,
            "result": record.result,
            "error": record.error,
            "event_types": [item.type for item in events],
            "artifacts": [
                {
                    key: item.get(key)
                    for key in ("artifact_id", "name", "media_type", "content_sha256", "uri")
                }
                for item in artifacts
            ],
            "verifications": [
                item.to_dict() if hasattr(item, "to_dict") else dict(item)
                for item in verifications
            ],
        }
        tasks = await asyncio.to_thread(
            self.store.list_runtime_tasks, run_id=record.run_id, limit=100
        )
        evidence["tasks"] = [
            {
                "task_id": item.task_id,
                "status": item.status,
                "result": item.result,
                "error": item.error,
            }
            for item in tasks
        ]
        return evidence, str(record.status), str(record.run_id)

    @staticmethod
    def _cost(output: dict[str, Any]) -> float | None:
        result = output.get("result")
        usage = output.get("usage")
        if usage is None and isinstance(result, dict):
            usage = result.get("usage")
        if not isinstance(usage, dict) or usage.get("cost_usd") is None:
            return None
        return float(usage["cost_usd"])
