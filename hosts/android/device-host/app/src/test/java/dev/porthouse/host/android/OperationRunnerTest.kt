package dev.porthouse.host.android

import dev.porthouse.host.android.executor.DeviceExecutor
import dev.porthouse.host.android.executor.JpegCodec
import dev.porthouse.host.android.runner.OperationRunner
import dev.porthouse.host.android.transport.ClaimResponse
import dev.porthouse.host.android.transport.ClaimedDelivery
import dev.porthouse.host.android.transport.CompletionResult
import dev.porthouse.host.android.transport.DeviceEvent
import dev.porthouse.host.android.transport.DeviceTransport
import dev.porthouse.host.android.transport.DeliveryEnvelope
import dev.porthouse.host.android.transport.EnvelopeCapability
import dev.porthouse.host.android.transport.EnvelopeExecution
import dev.porthouse.host.android.transport.EnvelopeSubject
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class OperationRunnerTest {

    private class FakeTransport : DeviceTransport {
        override val claimSessionId = "test-session-0001"
        var queued: List<ClaimedDelivery> = emptyList()
        val events = mutableListOf<Pair<String, DeviceEvent>>()
        val completions = mutableListOf<Pair<String, CompletionResult>>()
        var claimCalls = 0

        override suspend fun heartbeat(hostRevision: String, hostManifestDigest: String) {}
        override suspend fun claim(limit: Int, leaseSeconds: Int): ClaimResponse {
            claimCalls++
            val items = queued
            queued = emptyList()
            return ClaimResponse(items)
        }
        override suspend fun renew(deliveryId: String, claimVersion: Int, leaseSeconds: Int) {}
        override suspend fun appendEvents(deliveryId: String, claimVersion: Int, events: List<DeviceEvent>) {
            this.events += events.map { deliveryId to it }
        }
        override suspend fun complete(deliveryId: String, claimVersion: Int, result: CompletionResult) {
            completions += deliveryId to result
        }
    }

    private class FakeExecutor(var available: Boolean = true) : DeviceExecutor {
        val shellCalls = mutableListOf<List<String>>()
        var shellHandler: (List<String>) -> String = { "" }
        var screenshotBytes: ByteArray = ByteArray(0)

        override suspend fun shell(argv: List<String>): String {
            shellCalls += argv
            return shellHandler(argv)
        }
        override suspend fun screenshotPng(): ByteArray = screenshotBytes
        override fun isAvailable(): Boolean = available
    }

    private class FakeCodec(var output: ByteArray) : JpegCodec {
        var calls = 0
        override fun encodePngToJpeg(png: ByteArray, maxBytes: Int): ByteArray {
            calls++
            return output
        }
    }

    private fun delivery(op: String, vararg pairs: Pair<String, kotlinx.serialization.json.JsonElement>): ClaimedDelivery {
        val input = buildJsonObject {
            put("op", op)
            pairs.forEach { (k, v) -> put(k, v) }
        }
        return ClaimedDelivery(
            delivery_id = "delivery_1",
            invocation_id = "inv_1",
            run_id = "run_1",
            status = "claimed",
            claim_version = 3,
            request = DeliveryEnvelope(
                protocol_version = "1",
                capability = EnvelopeCapability("android.actuate", "1.0.0"),
                subject = EnvelopeSubject("user-a"),
                execution = EnvelopeExecution(
                    run_id = "run_1",
                    action_id = "action_1",
                    idempotency_key = "action:action_1",
                ),
                input = input,
            ),
        )
    }

    private fun int(v: Int) = kotlinx.serialization.json.JsonPrimitive(v)

    @Test
    fun tapCompletesWithExecutedArgvAndProgressEvent() = kotlinx.coroutines.test.runTest {
        val transport = FakeTransport()
        val executor = FakeExecutor()
        transport.queued = listOf(delivery("tap", "x" to int(540), "y" to int(1200)))
        val runner = OperationRunner(transport, executor, FakeCodec(ByteArray(0)))

        runner.runOnce()

        assertEquals(listOf(listOf("input", "tap", "540", "1200")), executor.shellCalls)
        val (id, result) = transport.completions.single()
        assertEquals("delivery_1", id)
        assertEquals("succeeded", result.status)
        assertEquals("inv_1", result.invocation_id)
        val executed = result.data!!["executed"]!!.let { element ->
            (element as kotlinx.serialization.json.JsonArray).map {
                (it as kotlinx.serialization.json.JsonPrimitive).content
            }
        }
        assertEquals(listOf("input", "tap", "540", "1200"), executed)
        assertTrue(transport.events.isNotEmpty())
    }

    @Test
    fun uiDumpParsesSampleXmlThroughFallback() = kotlinx.coroutines.test.runTest {
        val sample = FileResources.read("window_dump.sample.xml")
        val transport = FakeTransport()
        val executor = FakeExecutor()
        // /dev/tty path returns noise, forcing the sdcard fallback sequence.
        executor.shellHandler = { argv ->
            when {
                argv.contains("/dev/tty") -> "UI hierchary dumped to: /dev/tty"
                argv.first() == "uiautomator" -> "dumped ok"
                argv.first() == "cat" -> sample
                else -> ""
            }
        }
        transport.queued = listOf(delivery("ui_dump"))
        val runner = OperationRunner(transport, executor, FakeCodec(ByteArray(0)))

        runner.runOnce()

        val result = transport.completions.single().second
        assertEquals("succeeded", result.status)
        val data = result.data!!.toString()
        assertTrue("1080" in data && "About phone" in data)
        assertTrue(
            executor.shellCalls.any { call ->
                call.first() == "rm" && call.any { it.endsWith("window_dump.xml") }
            },
        )
    }

    @Test
    fun screenshotReturnsInlineJpegWithinBudget() = kotlinx.coroutines.test.runTest {
        val transport = FakeTransport()
        val executor = FakeExecutor().apply { screenshotBytes = byteArrayOf(1, 2, 3) }
        val codec = FakeCodec("fake-jpeg".toByteArray())
        transport.queued = listOf(delivery("screenshot"))
        val runner = OperationRunner(transport, executor, codec)

        runner.runOnce()

        val result = transport.completions.single().second
        assertEquals("succeeded", result.status)
        assertEquals(1, codec.calls)
        assertTrue(result.data!!["image_base64"].toString().isNotEmpty())
    }

    @Test
    fun lockedScreenFailsClosedWithRetryableError() = kotlinx.coroutines.test.runTest {
        val transport = FakeTransport()
        transport.queued = listOf(delivery("tap", "x" to int(1), "y" to int(2)))
        val runner = OperationRunner(
            transport,
            FakeExecutor(),
            FakeCodec(ByteArray(0)),
            screenPrecheck = { false },
        )

        runner.runOnce()

        val result = transport.completions.single().second
        assertEquals("failed", result.status)
        assertEquals("SCREEN_LOCKED", result.error!!.code)
        assertEquals(true, result.error!!.retryable)
    }

    @Test
    fun invalidOpFailsClosed() = kotlinx.coroutines.test.runTest {
        val transport = FakeTransport()
        transport.queued = listOf(delivery("launch_app", "package" to kotlinx.serialization.json.JsonPrimitive("com.evil; rm")))
        val runner = OperationRunner(transport, FakeExecutor(), FakeCodec(ByteArray(0)))

        runner.runOnce()

        val result = transport.completions.single().second
        assertEquals("failed", result.status)
        assertEquals("INVALID_PARAMETERS", result.error!!.code)
    }

    @Test
    fun unavailableExecutorFailsClosed() = kotlinx.coroutines.test.runTest {
        val transport = FakeTransport()
        transport.queued = listOf(delivery("wake"))
        val runner = OperationRunner(transport, FakeExecutor(available = false), FakeCodec(ByteArray(0)))

        runner.runOnce()

        val result = transport.completions.single().second
        assertEquals("failed", result.status)
        assertEquals("EXECUTOR_UNAVAILABLE", result.error!!.code)
    }
}
