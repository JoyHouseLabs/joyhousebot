package dev.porthouse.host.android.transport

import java.io.IOException
import java.security.KeyStore
import java.util.UUID
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManagerFactory
import javax.net.ssl.X509TrustManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response

/**
 * Outbound HTTPS client for the Device Host device API. The phone only ever
 * initiates requests; no inbound ports, NAT-friendly by design.
 */
class PorthouseTransport(
    private val baseUrl: String,
    private val deviceId: String,
    private val tokenProvider: () -> String,
    private val client: OkHttpClient = defaultClient(),
) : DeviceTransport {

    override val claimSessionId: String = "android-" + UUID.randomUUID().toString().replace("-", "").take(24)

    private val jsonMedia = "application/json; charset=utf-8".toMediaType()

    override suspend fun heartbeat(hostRevision: String, hostManifestDigest: String) {
        post(
            "/v1/device-host/heartbeat",
            deviceJson.encodeToString(HeartbeatRequest.serializer(), HeartbeatRequest(hostRevision, hostManifestDigest)),
        )
    }

    override suspend fun claim(limit: Int, leaseSeconds: Int): ClaimResponse {
        val body = deviceJson.encodeToString(
            ClaimRequest.serializer(),
            ClaimRequest(claimSessionId, limit, leaseSeconds.coerceIn(TransportLimits.MIN_LEASE_SECONDS, TransportLimits.MAX_LEASE_SECONDS)),
        )
        val payload = post("/v1/device-host/operations:claim", body)
        return deviceJson.decodeFromString(ClaimResponse.serializer(), payload)
    }

    override suspend fun renew(deliveryId: String, claimVersion: Int, leaseSeconds: Int) {
        post(
            "/v1/device-host/operations/$deliveryId:heartbeat",
            deviceJson.encodeToString(
                RenewRequest.serializer(),
                RenewRequest(claimSessionId, claimVersion, leaseSeconds),
            ),
        )
    }

    override suspend fun appendEvents(deliveryId: String, claimVersion: Int, events: List<DeviceEvent>) {
        post(
            "/v1/device-host/operations/$deliveryId/events:append",
            deviceJson.encodeToString(
                EventsRequest.serializer(),
                EventsRequest(claimSessionId, claimVersion, events),
            ),
        )
    }

    override suspend fun complete(deliveryId: String, claimVersion: Int, result: CompletionResult) {
        post(
            "/v1/device-host/operations/$deliveryId:complete",
            deviceJson.encodeToString(
                CompleteRequest.serializer(),
                CompleteRequest(claimSessionId, claimVersion, result),
            ),
        )
    }

    private suspend fun post(path: String, body: String): String = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url(baseUrl.trimEnd('/') + path)
            .header("Authorization", "Bearer ${tokenProvider()}")
            .header("X-Porthouse-Device-ID", deviceId)
            .post(body.toRequestBody(jsonMedia))
            .build()
        val response = try {
            client.newCall(request).execute()
        } catch (exc: IOException) {
            throw DeviceTransport.TransportError(-1, exc.message ?: "network failure", exc.message ?: "network failure")
        }
        response.use { parse(it) }
    }

    private fun parse(response: Response): String {
        val text = response.body?.string().orEmpty()
        if (response.code == 401) {
            throw DeviceTransport.Unauthorized("device token was rejected")
        }
        if (!response.isSuccessful) {
            throw DeviceTransport.TransportError(response.code, text, "HTTP ${response.code}: ${text.take(200)}")
        }
        return text
    }

    companion object {
        fun defaultClient(): OkHttpClient = OkHttpClient.Builder()
            .callTimeout(java.time.Duration.ofSeconds(30))
            .connectTimeout(java.time.Duration.ofSeconds(10))
            .build()

        /**
         * Debug pairing against a local Runtime with a self-signed CA:
         * trust only the pinned CA from assets (assets/runtime-ca.pem).
         */
        fun pinnedClient(caPem: java.io.InputStream): Pair<OkHttpClient, X509TrustManager> {
            val certificate = java.security.cert.CertificateFactory.getInstance("X.509")
                .generateCertificates(caPem)
                .first() as java.security.cert.X509Certificate
            val keyStore = KeyStore.getInstance(KeyStore.getDefaultType()).apply {
                load(null, null)
                setCertificateEntry("runtime-ca", certificate)
            }
            val factory = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm())
            factory.init(keyStore)
            val trustManager = factory.trustManagers.filterIsInstance<X509TrustManager>().first()
            val sslContext = SSLContext.getInstance("TLS").apply {
                init(null, arrayOf(trustManager), null)
            }
            return OkHttpClient.Builder()
                .sslSocketFactory(sslContext.socketFactory, trustManager)
                .build() to trustManager
        }
    }
}
