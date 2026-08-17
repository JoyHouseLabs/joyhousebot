package dev.porthouse.host.android.runner

import dev.porthouse.host.android.executor.DeviceExecutor
import dev.porthouse.host.android.executor.JpegCodec
import dev.porthouse.host.android.executor.OpSpec
import dev.porthouse.host.android.executor.Parsers
import dev.porthouse.host.android.transport.ClaimedDelivery
import dev.porthouse.host.android.transport.CompletionResult
import dev.porthouse.host.android.transport.DeviceEvent
import dev.porthouse.host.android.transport.DeviceTransport
import dev.porthouse.host.android.transport.ErrorBody
import dev.porthouse.host.android.transport.TransportLimits
import dev.porthouse.host.android.transport.deviceJson
import java.util.Base64
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * Executes one claimed delivery against the fixed op contract and reports
 * bounded evidence. Every failure path is fail-closed: unknown ops, locked
 * screens, oversized payloads and executor outages complete with an explicit
 * error result instead of guessing success.
 */
class OperationRunner(
    private val transport: DeviceTransport,
    private val executor: DeviceExecutor,
    private val jpeg: JpegCodec,
    private val screenPrecheck: suspend () -> Boolean = { true },
) {

    // Base64 inflates by 4/3; keep the encoded image inside the 4 MiB result.
    private val maxJpegBytes = (TransportLimits.MAX_RESULT_BYTES * 3 / 4 * 9 / 10).toInt()

    suspend fun runOnce(limit: Int = 5): Int {
        val claimed = transport.claim(limit = limit)
        claimed.items.forEach { execute(it) }
        return claimed.items.size
    }

    suspend fun execute(item: ClaimedDelivery) {
        val version = item.claim_version
        val input = item.request?.input ?: buildJsonObject { }
        val op = input.stringOrNullOf("op")
        try {
            if (op == null) throw OpSpec.OpError("INVALID_PARAMETERS", "op is missing")
            if (!executor.isAvailable()) {
                completeFailed(item, version, "EXECUTOR_UNAVAILABLE", "Shizuku service is not bound", retryable = true)
                return
            }
            if (!screenPrecheck()) {
                appendEvent(item.delivery_id, version, "manual_required", "device is locked or unavailable")
                completeFailed(item, version, "SCREEN_LOCKED", "device screen is locked", retryable = true)
                return
            }
            val data = dispatch(op, input, item)
            transport.complete(
                item.delivery_id,
                version,
                CompletionResult(
                    invocation_id = item.invocation_id,
                    status = "succeeded",
                    summary = "$op completed on device",
                    data = data,
                ),
            )
        } catch (exc: OpSpec.OpError) {
            completeFailed(item, version, exc.code, exc.message ?: "invalid op", retryable = false)
        } catch (exc: Parsers.ParseError) {
            completeFailed(item, version, exc.code, exc.message ?: "parse failed", retryable = false)
        } catch (exc: DeviceExecutor.ExecError) {
            completeFailed(item, version, "DEVICE_EXEC_FAILED", exc.message ?: "exec failed", retryable = true)
        } catch (exc: Exception) {
            completeFailed(item, version, "DEVICE_INTERNAL_ERROR", exc.message ?: "unknown error", retryable = true)
        }
    }

    private suspend fun dispatch(op: String, input: JsonObject, item: ClaimedDelivery): JsonObject {
        return when (op) {
            "ui_dump" -> {
                appendEvent(item.delivery_id, item.claim_version, "progress", "dumping ui hierarchy")
                val maxNodes = input.numberOrNullOf("max_nodes")?.toInt() ?: 200
                val xml = uiDumpXml()
                val dump = Parsers.parseUiDump(xml, maxNodes.coerceIn(1, 1000))
                buildJsonObject {
                    put("screen", buildJsonObject {
                        put("width", dump.screenWidth)
                        put("height", dump.screenHeight)
                    })
                    put("nodes", kotlinx.serialization.json.JsonArray(dump.nodes))
                    put("truncated", dump.truncated)
                }
            }
            "screenshot" -> {
                appendEvent(item.delivery_id, item.claim_version, "progress", "capturing screenshot")
                val png = executor.screenshotPng()
                val jpegBytes = jpeg.encodePngToJpeg(png, maxJpegBytes)
                if (jpegBytes.size > maxJpegBytes) {
                    throw Parsers.ParseError("RESULT_TOO_LARGE", "screenshot exceeds inline result budget")
                }
                buildJsonObject {
                    put("media_type", "image/jpeg")
                    put("size_bytes", jpegBytes.size)
                    put("image_base64", Base64.getEncoder().encodeToString(jpegBytes))
                }
            }
            "screen_state" -> {
                val (wakefulness, screenOn) = Parsers.parseScreenState(
                    executor.shell(OpSpec.buildArgv("screen_state", input)),
                )
                buildJsonObject {
                    put("wakefulness", wakefulness)
                    put("screen_on", screenOn)
                }
            }
            "current_app" -> {
                val (pkg, activity, component) = Parsers.parseCurrentApp(
                    executor.shell(OpSpec.buildArgv("current_app", input)),
                )
                buildJsonObject {
                    put("package", pkg)
                    put("activity", activity)
                    put("component", component)
                }
            }
            // All actuate ops share the fixed-argv path; the Runtime already
            // required human approval before this delivery could exist.
            else -> {
                val argv = OpSpec.buildArgv(op, input)
                executor.shell(argv)
                appendEvent(item.delivery_id, item.claim_version, "progress", "executed ${argv.joinToString(" ")}")
                buildJsonObject {
                    put("executed", kotlinx.serialization.json.JsonArray(argv.map { kotlinx.serialization.json.JsonPrimitive(it) }))
                }
            }
        }
    }

    private suspend fun uiDumpXml(): String {
        val output = executor.shell(OpSpec.buildArgv("ui_dump", buildJsonObject { }))
        return try {
            Parsers.extractUiXml(output)
        } catch (_: Parsers.ParseError) {
            // Some builds refuse /dev/tty; mirror the probe's sdcard fallback.
            executor.shell(listOf("uiautomator", "dump", OpSpec.UI_DUMP_DEVICE_PATH))
            val xml = executor.shell(listOf("cat", OpSpec.UI_DUMP_DEVICE_PATH))
            executor.shell(listOf("rm", "-f", OpSpec.UI_DUMP_DEVICE_PATH))
            Parsers.extractUiXml(xml)
        }
    }

    private suspend fun appendEvent(deliveryId: String, claimVersion: Int, type: String, summary: String) {
        transport.appendEvents(
            deliveryId,
            claimVersion,
            listOf(
                DeviceEvent(
                    event_id = "evt-$deliveryId-${type.hashCode()}-${summary.hashCode()}",
                    sequence = nextSequence(deliveryId),
                    event_type = type,
                    summary = summary.take(500),
                ),
            ),
        )
    }

    private val sequences = HashMap<String, Int>()

    private fun nextSequence(deliveryId: String): Int {
        val next = (sequences[deliveryId] ?: -1) + 1
        sequences[deliveryId] = next
        return next
    }

    private suspend fun completeFailed(
        item: ClaimedDelivery,
        claimVersion: Int,
        code: String,
        message: String,
        retryable: Boolean,
    ) {
        transport.complete(
            item.delivery_id,
            claimVersion,
            CompletionResult(
                invocation_id = item.invocation_id,
                status = "failed",
                summary = message.take(2000),
                error = ErrorBody(code, message.take(2000), retryable),
            ),
        )
    }
}

private fun JsonObject.stringOrNullOf(key: String): String? =
    (this[key] as? kotlinx.serialization.json.JsonPrimitive)?.takeIf { it.isString }?.content

private fun JsonObject.numberOrNullOf(key: String): Long? =
    (this[key] as? kotlinx.serialization.json.JsonPrimitive)?.let {
        it.content.toLongOrNull()
    }
