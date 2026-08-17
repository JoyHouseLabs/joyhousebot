package dev.porthouse.host.android.transport

import kotlinx.serialization.SerialName
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonElement

/**
 * Device Host Transport v1 DTOs. Field names and size limits mirror
 * porthouse/api/device_host_schemas.py:
 * envelope <= 1 MiB, result <= 4 MiB, event payload <= 32 KiB,
 * lease 10..300s. The device header is X-Porthouse-Device-ID.
 */
object TransportLimits {
    const val MAX_RESULT_BYTES = 4L * 1024 * 1024
    const val MAX_EVENT_PAYLOAD_BYTES = 32L * 1024
    const val MIN_LEASE_SECONDS = 10
    const val MAX_LEASE_SECONDS = 300
}

val deviceJson = kotlinx.serialization.json.Json {
    ignoreUnknownKeys = true
    encodeDefaults = false
    explicitNulls = false
}

@kotlinx.serialization.Serializable
data class HeartbeatRequest(
    val host_revision: String,
    val host_manifest_digest: String,
)

@kotlinx.serialization.Serializable
data class ClaimRequest(
    val claim_session_id: String,
    val limit: Int = 5,
    val lease_seconds: Int = 60,
)

@kotlinx.serialization.Serializable
data class EnvelopeCapability(
    val capability_id: String,
    val version: String,
    val implementation_digest: String? = null,
)

@kotlinx.serialization.Serializable
data class EnvelopeSubject(
    val user_id: String,
    val agent_id: String? = null,
    val session_id: String? = null,
)

@kotlinx.serialization.Serializable
data class EnvelopeExecution(
    val run_id: String,
    val root_run_id: String? = null,
    val task_id: String? = null,
    val request_id: String? = null,
    val action_id: String,
    val idempotency_key: String,
    val request_digest: String? = null,
)

@kotlinx.serialization.Serializable
data class DeliveryEnvelope(
    val protocol_version: String,
    val capability: EnvelopeCapability,
    val subject: EnvelopeSubject,
    val execution: EnvelopeExecution,
    val authorization: JsonObject? = null,
    val input: JsonObject,
)

@kotlinx.serialization.Serializable
data class ClaimedDelivery(
    val delivery_id: String,
    val invocation_id: String,
    val run_id: String,
    val status: String,
    val claim_version: Int,
    val deadline_at: String? = null,
    val request: DeliveryEnvelope? = null,
)

@kotlinx.serialization.Serializable
data class ClaimResponse(val items: List<ClaimedDelivery> = emptyList())

@kotlinx.serialization.Serializable
data class DeviceEvent(
    val event_id: String,
    val sequence: Int,
    val event_type: String,
    val summary: String = "",
    val payload: JsonObject? = null,
)

@kotlinx.serialization.Serializable
data class EventsRequest(
    val claim_session_id: String,
    val claim_version: Int,
    val events: List<DeviceEvent>,
)

@kotlinx.serialization.Serializable
data class RenewRequest(
    val claim_session_id: String,
    val claim_version: Int,
    val lease_seconds: Int = 60,
)

@kotlinx.serialization.Serializable
data class ErrorBody(
    val code: String,
    val message: String,
    val retryable: Boolean = false,
)

@kotlinx.serialization.Serializable
data class CompletionResult(
    val invocation_id: String,
    val status: String,
    val summary: String,
    val data: JsonObject? = null,
    val artifacts: List<JsonObject> = emptyList(),
    val error: ErrorBody? = null,
    val operation: JsonObject? = null,
)

@kotlinx.serialization.Serializable
data class CompleteRequest(
    val claim_session_id: String,
    val claim_version: Int,
    val result: CompletionResult,
)

@kotlinx.serialization.Serializable
data class DeliveryEnvelopeDto(val delivery: JsonElement? = null)
