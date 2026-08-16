"""Exact-model invocation with transactional Host budgets and full traces."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Callable
from uuid import uuid4

from loguru import logger

from porthouse.application.model_grants import model_grant_token_fingerprint
from porthouse.config.schema import ProviderConfig
from porthouse.domain.model_providers import materialize_model_provider
from porthouse.providers.factory import create_model_provider
from porthouse.providers.usage import missing_usage


class ModelGatewayError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400, code: str = "invalid_request"):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


class HostModelGatewayService:
    def __init__(
        self,
        *,
        store: Any,
        config: Any,
        provider_factory: Callable[..., Any] = create_model_provider,
    ) -> None:
        self.store = store
        self.config = config
        self.provider_factory = provider_factory

    async def chat(self, *, token: str, request: dict[str, Any]) -> dict[str, Any]:
        grant = self._authenticate(token)
        if request["model"] != grant.model_id:
            raise ModelGatewayError("grant does not authorize the requested exact model", status_code=403, code="forbidden")
        revision, model = self._exact_model(grant)
        max_tokens = int(request["max_tokens"])
        output_limit = int(model.get("max_output_tokens") or 0)
        if output_limit and max_tokens > output_limit:
            raise ModelGatewayError("max_tokens exceeds the published model limit")
        input_upper = len(_canonical({"messages": request["messages"], "tools": request["tools"]})) + 1024
        context_window = int(model.get("context_window") or 0)
        if context_window and input_upper + max_tokens > context_window:
            raise ModelGatewayError("conservative request bound exceeds the model context window")
        token_upper = input_upper + max_tokens
        cost_upper = self._cost_upper(grant, model, input_upper, max_tokens)
        reservation, created = self.store.reserve_host_model_budget(
            grant.grant_id,
            reservation_id=f"model_reservation_{uuid4().hex}",
            request_id=request["request_id"],
            reserved_tokens=token_upper,
            reserved_cost_micros=cost_upper,
            reservation_seconds=min(
                3600,
                max(60, int(dict(revision["configuration"]).get("request_timeout_seconds") or 120) + 60),
            ),
        )
        if reservation is None:
            raise ModelGatewayError("model grant budget or concurrency is exhausted", status_code=429, code="budget_exhausted")
        if not created:
            if reservation.status == "settled" and reservation.response is not None:
                return dict(reservation.response)
            raise ModelGatewayError("model request_id has already been consumed", status_code=409, code="duplicate_request")
        provider = None
        observation = None
        provider_called = False
        settled = False
        try:
            observation = self._start_observation(grant, request)
            provider = self._provider(grant, revision)
            provider_called = True
            response = await provider.chat(
                messages=[dict(item) for item in request["messages"]],
                tools=[dict(item) for item in request["tools"]] or None,
                model=grant.model_id,
                max_tokens=max_tokens,
                temperature=float(request["temperature"]),
            )
            usage, tokens, cost = self._settled_usage(
                response.usage,
                token_upper=token_upper,
                cost_upper=cost_upper,
                enforce_cost=grant.cost_budget_micros > 0,
            )
            payload = {
                "request_id": request["request_id"],
                "model": grant.model_id,
                "provider_revision_id": grant.provider_revision_id,
                "content": response.content,
                "tool_calls": [
                    {"id": item.id, "name": item.name, "arguments": item.arguments}
                    for item in response.tool_calls
                ],
                "finish_reason": response.finish_reason,
                "usage": usage,
            }
            self.store.settle_host_model_budget(
                reservation.reservation_id,
                actual_tokens=tokens,
                actual_cost_micros=cost,
                usage=usage,
                response=payload,
            )
            settled = True
            try:
                self._finish_observation(observation, payload=payload, usage=usage)
            except Exception:
                logger.exception(
                    "model gateway observation finalization failed after budget settlement"
                )
            return payload
        except ModelGatewayError:
            if not settled:
                self._fail_or_release(
                    reservation,
                    observation,
                    token_upper,
                    cost_upper,
                    provider_called=provider_called,
                )
            raise
        except Exception as exc:
            if not settled:
                self._fail_or_release(
                    reservation,
                    observation,
                    token_upper,
                    cost_upper,
                    provider_called=provider_called,
                    exc=exc,
                )
            raise ModelGatewayError(
                "model provider invocation failed",
                status_code=502,
                code="provider_error",
            ) from exc
        finally:
            if provider is not None:
                close = getattr(provider, "close", None)
                if callable(close):
                    try:
                        result = close()
                        if hasattr(result, "__await__"):
                            await result
                    except Exception as exc:
                        logger.warning("model gateway provider close failed: {}", type(exc).__name__)

    def _authenticate(self, token: str) -> Any:
        if not token.startswith("jhm_") or len(token) < 40:
            raise ModelGatewayError("invalid model grant", status_code=401, code="unauthorized")
        grant = self.store.authenticate_host_model_grant(
            token_fingerprint=model_grant_token_fingerprint(token)
        )
        if grant is None:
            raise ModelGatewayError("invalid, expired, or inactive model grant", status_code=401, code="unauthorized")
        return grant

    def _exact_model(self, grant: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        provider = self.store.get_model_provider(grant.provider_id)
        revision = self.store.get_model_provider_revision(
            grant.provider_id,
            grant.provider_revision_id,
        )
        if provider is None or revision is None or revision["status"] not in {
            "published",
            "retired",
        }:
            raise ModelGatewayError("authorized provider revision is no longer active", status_code=409, code="revision_unavailable")
        model = next(
            (
                dict(item)
                for item in dict(revision["configuration"]).get("models") or ()
                if item.get("model_id") == grant.model_id
                and item.get("kind", "llm") == "llm"
                and item.get("enabled", True)
            ),
            None,
        )
        if model is None:
            raise ModelGatewayError("authorized model is no longer active", status_code=409, code="revision_unavailable")
        return revision, model

    @staticmethod
    def _cost_upper(grant: Any, model: dict[str, Any], input_tokens: int, output_tokens: int) -> int:
        if grant.cost_budget_micros == 0:
            return 0
        input_rate = model.get("input_cost_per_million_tokens")
        output_rate = model.get("output_cost_per_million_tokens")
        if input_rate is None or output_rate is None:
            raise ModelGatewayError("a cost-budgeted grant requires exact catalog pricing", code="pricing_missing")
        return int(math.ceil(input_tokens * float(input_rate) + output_tokens * float(output_rate)))

    def _provider(self, grant: Any, revision: dict[str, Any]) -> Any:
        raw_configuration = dict(revision["configuration"])
        extension_id = str(raw_configuration.get("extension_id") or "")
        if not self.store.is_plugin_execution_enabled(extension_id):
            raise ModelGatewayError(
                "authorized provider extension is not execution-enabled",
                status_code=409,
                code="extension_unavailable",
            )
        configuration = materialize_model_provider(raw_configuration)
        copied = type(self.config).model_validate(self.config.model_dump())
        copied.providers.default_provider = grant.provider_id
        copied.providers.settings[grant.provider_id] = ProviderConfig(
            api_key=str(configuration.get("api_key") or ""),
            api_base=str(configuration.get("api_base") or ""),
            extra_headers=dict(configuration.get("extra_headers") or {}),
            request_timeout_seconds=float(configuration.get("request_timeout_seconds") or 120),
            models=[dict(item) for item in configuration.get("models") or []],
            revision_id=grant.provider_revision_id,
        )
        return self.provider_factory(
            config=copied,
            model=grant.model_id,
            provider_name=grant.provider_id,
            request_timeout_seconds=float(configuration.get("request_timeout_seconds") or 120),
        )

    @staticmethod
    def _settled_usage(
        raw: dict[str, Any],
        *,
        token_upper: int,
        cost_upper: int,
        enforce_cost: bool,
    ) -> tuple[dict[str, Any], int, int]:
        usage = dict(raw or missing_usage())
        exact = usage.get("usage_status") in {"exact", "partial"}
        tokens = int(usage.get("billed_total_tokens") or usage.get("total_tokens") or 0)
        cost = int(math.ceil(float(usage.get("cost_usd") or 0) * 1_000_000))
        if not exact or tokens <= 0:
            usage.update(
                usage_status="missing",
                billing_status="conservative_upper",
                budget_accounting="reserved_upper",
            )
            return usage, token_upper, cost_upper
        if tokens > token_upper or (enforce_cost and cost > cost_upper):
            raise ModelGatewayError("provider usage exceeded its conservative reservation", status_code=502, code="usage_bound_exceeded")
        if not enforce_cost:
            cost = 0
        if usage.get("billing_status") == "missing" and cost_upper:
            usage["budget_accounting"] = "reserved_cost_upper"
            cost = cost_upper
        return usage, tokens, cost

    def _start_observation(
        self, grant: Any, request: dict[str, Any]
    ) -> tuple[str, str, str]:
        request_blob = self.store.put_trace_blob(
            run_id=grant.run_id,
            kind="model_request",
            content=request,
        )
        span = self.store.start_execution_span(
            trace_id=grant.run_id,
            run_id=grant.run_id,
            task_id=grant.task_id,
            span_kind="model",
            name="host_model_gateway.chat",
            attributes={"grant_id": grant.grant_id, "delivery_id": grant.delivery_id},
        )
        invocation = self.store.create_model_invocation(
            run_id=grant.run_id,
            task_id=grant.task_id,
            span_id=span.span_id,
            provider=grant.provider_id,
            model=grant.model_id,
            operation="host_gateway.chat",
            request_blob_id=request_blob.blob_id,
            request_hash=hashlib.sha256(_canonical(request)).hexdigest(),
        )
        return invocation.invocation_id, span.span_id, grant.run_id

    def _finish_observation(
        self,
        observation: tuple[str, str, str],
        *,
        payload: dict[str, Any],
        usage: dict[str, Any],
    ) -> None:
        invocation_id, _, run_id = observation
        response_blob = self.store.put_trace_blob(
            run_id=run_id,
            invocation_id=invocation_id,
            kind="model_response",
            content=payload,
        )
        self.store.finish_model_invocation(
            invocation_id,
            response_blob_id=response_blob.blob_id,
            response_hash=hashlib.sha256(_canonical(payload)).hexdigest(),
            finish_reason=payload["finish_reason"],
            usage=usage,
            cost_usd=float(usage.get("cost_usd") or 0),
            reasoning_availability="unavailable",
        )

    def _fail_or_release(
        self,
        reservation: Any,
        observation: tuple[str, str, str] | None,
        token_upper: int,
        cost_upper: int,
        *,
        provider_called: bool,
        exc: Exception | None = None,
    ) -> None:
        usage = {
            "usage_status": "missing",
            "billing_status": "conservative_upper",
            "budget_accounting": "reserved_upper",
        }
        if provider_called:
            self.store.settle_host_model_budget(
                reservation.reservation_id,
                actual_tokens=token_upper,
                actual_cost_micros=cost_upper,
                usage=usage,
            )
        else:
            self.store.release_host_model_budget(reservation.reservation_id)
        if observation is not None:
            self.store.finish_model_invocation(
                observation[0],
                status="failed",
                usage=usage,
                cost_usd=cost_upper / 1_000_000,
                error={
                    "code": "model_gateway_failed",
                    "message": type(exc).__name__ if exc else "request rejected",
                },
                reasoning_availability="unavailable",
            )


__all__ = ["HostModelGatewayService", "ModelGatewayError"]
