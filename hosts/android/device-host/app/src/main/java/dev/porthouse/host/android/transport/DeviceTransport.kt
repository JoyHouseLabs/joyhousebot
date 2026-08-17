package dev.porthouse.host.android.transport

/** Transport surface the runner depends on; fake in unit tests. */
interface DeviceTransport {
    val claimSessionId: String

    suspend fun heartbeat(hostRevision: String, hostManifestDigest: String)

    suspend fun claim(limit: Int = 5, leaseSeconds: Int = 60): ClaimResponse

    suspend fun renew(deliveryId: String, claimVersion: Int, leaseSeconds: Int = 60)

    suspend fun appendEvents(
        deliveryId: String,
        claimVersion: Int,
        events: List<DeviceEvent>,
    )

    suspend fun complete(
        deliveryId: String,
        claimVersion: Int,
        result: CompletionResult,
    )

    /** Terminal auth failure: token revoked/rotated; the loop must stop. */
    class Unauthorized(message: String) : Exception(message)

    class TransportError(
        val httpStatus: Int,
        val body: String,
        message: String,
    ) : Exception(message)
}
