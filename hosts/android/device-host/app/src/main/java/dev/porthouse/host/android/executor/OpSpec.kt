package dev.porthouse.host.android.executor

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.longOrNull

/**
 * Fixed op -> argv mapping. This is the shared golden contract: it must stay
 * byte-identical with hosts/android/probe/android_probe.py and
 * extensions/capability-android-device input schemas.
 * `OpSpecTest` pins this class against fixtures/op_contract.json.
 *
 * There is intentionally no free-form command surface: every actuate/observe
 * op resolves to one argv template or fails closed.
 */
object OpSpec {

    val OBSERVE_OPS = setOf("ui_dump", "screenshot", "screen_state", "current_app")
    val ACTUATE_OPS = setOf("tap", "swipe", "input_text", "press_key", "launch_app", "wake")

    val PRESS_KEYS: Set<String> = setOf(
        "BACK", "HOME", "MENU", "ENTER", "DEL", "TAB", "ESC", "SPACE",
        "PAGE_UP", "PAGE_DOWN", "MOVE_HOME", "MOVE_END",
        "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT",
        "VOLUME_UP", "VOLUME_DOWN", "VOLUME_MUTE",
        "POWER", "APP_SWITCH",
        "MEDIA_PLAY", "MEDIA_PAUSE", "MEDIA_NEXT", "MEDIA_PREVIOUS",
    )

    private val PACKAGE_RE = Regex("""^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$""")
    private val ACTIVITY_RE = Regex("""^[A-Za-z0-9_.$]+$""")
    private val INPUT_TEXT_RE = Regex(
        """^[A-Za-z0-9 @%:;,.\-_+=/()?!'"<>#\$&*]+$"""
    )
    const val MAX_TEXT_CHARS = 500
    const val UI_DUMP_DEVICE_PATH = "/sdcard/window_dump.xml"

    class OpError(val code: String, message: String) : Exception(message)

    fun buildArgv(op: String, input: JsonObject): List<String> = when (op) {
        "ui_dump" -> listOf("uiautomator", "dump", "/dev/tty")
        "screen_state" -> listOf("dumpsys", "power")
        "current_app" -> listOf("dumpsys", "window")
        "tap" -> listOf(
            "input", "tap",
            requireCoord(input, "x").toString(),
            requireCoord(input, "y").toString(),
        )
        "swipe" -> {
            val duration = input.numberOrNull("duration_ms")?.toInt() ?: 300
            if (duration !in 0..10_000) {
                throw OpError("INVALID_PARAMETERS", "duration_ms must be within 0..10000")
            }
            listOf(
                "input", "swipe",
                requireCoord(input, "x1").toString(),
                requireCoord(input, "y1").toString(),
                requireCoord(input, "x2").toString(),
                requireCoord(input, "y2").toString(),
                duration.toString(),
            )
        }
        "input_text" -> {
            val text = input.stringOrNull("text")
            if (text.isNullOrEmpty() || text.length > MAX_TEXT_CHARS) {
                throw OpError("INVALID_PARAMETERS", "text must be 1..$MAX_TEXT_CHARS characters")
            }
            if (!INPUT_TEXT_RE.matches(text)) {
                throw OpError("INVALID_CHARACTERS", "text outside the shared input-text charset")
            }
            listOf("input", "text", text.replace(" ", "%s"))
        }
        "press_key" -> {
            val key = input.stringOrNull("key")?.trim()?.uppercase()
            if (key == null || key !in PRESS_KEYS) {
                throw OpError("INVALID_PARAMETERS", "unsupported key")
            }
            listOf("input", "keyevent", "KEYCODE_$key")
        }
        "wake" -> listOf("input", "keyevent", "KEYCODE_WAKEUP")
        "launch_app" -> {
            val pkg = input.stringOrNull("package")?.trim().orEmpty()
            if (!PACKAGE_RE.matches(pkg)) {
                throw OpError("INVALID_PARAMETERS", "invalid package name")
            }
            val activity = input.stringOrNull("activity")?.trim().orEmpty()
            if (activity.isNotEmpty()) {
                if (!ACTIVITY_RE.matches(activity)) {
                    throw OpError("INVALID_PARAMETERS", "invalid activity name")
                }
                listOf("am", "start", "-n", "$pkg/$activity")
            } else {
                listOf("monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1")
            }
        }
        else -> throw OpError("INVALID_PARAMETERS", "unsupported op: $op")
    }

    private fun requireCoord(input: JsonObject, name: String): Long {
        val value = input.numberOrNull(name)
            ?: throw OpError("INVALID_PARAMETERS", "$name is required")
        if (value < 0) {
            throw OpError("INVALID_PARAMETERS", "$name must be >= 0")
        }
        return value
    }
}

internal fun JsonObject.stringOrNull(key: String): String? =
    (this[key] as? JsonPrimitive)?.takeIf { it.isString }?.content

internal fun JsonObject.numberOrNull(key: String): Long? =
    (this[key] as? JsonPrimitive)?.longOrNull

internal fun JsonObject.booleanOrNull(key: String): Boolean? =
    (this[key] as? JsonPrimitive)?.booleanOrNull
