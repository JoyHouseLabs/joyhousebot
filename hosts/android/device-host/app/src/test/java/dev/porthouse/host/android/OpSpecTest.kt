package dev.porthouse.host.android

import dev.porthouse.host.android.executor.OpSpec
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

/**
 * Pins the on-device argv mapping against the shared golden contract
 * (synced from hosts/android/probe/fixtures/op_contract.json).
 */
class OpSpecTest {

    private val contract: Map<String, List<String>> =
        Json.decodeFromString(
            FileResources.read("op_contract.json"),
        )

    @Test
    fun buildArgv_matchesGoldenContract() {
        val cases: Map<String, Pair<String, JsonObject>> = mapOf(
            "ui_dump" to ("ui_dump" to buildJsonObject { }),
            "screen_state" to ("screen_state" to buildJsonObject { }),
            "current_app" to ("current_app" to buildJsonObject { }),
            "tap" to ("tap" to buildJsonObject {
                put("x", 540); put("y", 1200)
            }),
            "swipe" to ("swipe" to buildJsonObject {
                put("x1", 540); put("y1", 1800); put("x2", 540); put("y2", 600)
                put("duration_ms", 250)
            }),
            "input_text" to ("input_text" to buildJsonObject { put("text", "hello world") }),
            "press_key" to ("press_key" to buildJsonObject { put("key", "back") }),
            "launch_app" to ("launch_app" to buildJsonObject {
                put("package", "com.android.settings")
            }),
            "launch_app_activity" to ("launch_app" to buildJsonObject {
                put("package", "com.android.settings")
                put("activity", ".Settings\$NetworkDashboardActivity")
            }),
            "wake" to ("wake" to buildJsonObject { }),
        )
        cases.forEach { (name, case) ->
            assertEquals(name, contract[name], OpSpec.buildArgv(case.first, case.second))
        }
    }

    @Test
    fun invalidOpsFailClosed() {
        assertThrows(OpSpec.OpError::class.java) {
            OpSpec.buildArgv("shell", buildJsonObject { })
        }
        assertThrows(OpSpec.OpError::class.java) {
            OpSpec.buildArgv("tap", buildJsonObject { put("x", -1); put("y", 2) })
        }
        assertThrows(OpSpec.OpError::class.java) {
            OpSpec.buildArgv("press_key", buildJsonObject { put("key", "SHELL") })
        }
        assertThrows(OpSpec.OpError::class.java) {
            OpSpec.buildArgv("launch_app", buildJsonObject { put("package", "com.evil; rm") })
        }
        assertThrows(OpSpec.OpError::class.java) {
            OpSpec.buildArgv("input_text", buildJsonObject { put("text", "你好") })
        }
        assertThrows(OpSpec.OpError::class.java) {
            OpSpec.buildArgv("input_text", buildJsonObject { put("text", "") })
        }
    }

    @Test
    fun observeAndActuateSetsAreClosed() {
        assertEquals(
            setOf("ui_dump", "screenshot", "screen_state", "current_app"),
            OpSpec.OBSERVE_OPS,
        )
        assertEquals(
            setOf("tap", "swipe", "input_text", "press_key", "launch_app", "wake"),
            OpSpec.ACTUATE_OPS,
        )
    }
}

/** Minimal classpath resource reader that works on plain JVM unit tests. */
object FileResources {
    fun read(name: String): String =
        javaClass.classLoader!!.getResourceAsStream(name)!!
            .readBytes()
            .toString(Charsets.UTF_8)
}
