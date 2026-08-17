package dev.porthouse.host.android.executor

import org.w3c.dom.Element
import java.io.ByteArrayInputStream
import javax.xml.parsers.DocumentBuilderFactory
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * Pure parsers for device output. Output shapes mirror the Phase-0 probe
 * contract so Run evidence looks identical regardless of which executor ran.
 */
object Parsers {

    class ParseError(val code: String, message: String) : Exception(message)

    private val BOUNDS_RE = Regex("""\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]""")
    private val WAKEFULNESS_RE = Regex("""mWakefulness=(Awake|Asleep|Dozing|Dreaming)""")
    private val COMPONENT_RE =
        Regex("""([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+/[^ }\s]+)""")
    private const val MAX_FIELD_CHARS = 120

    data class UiDump(
        val screenWidth: Int,
        val screenHeight: Int,
        val nodes: List<JsonObject>,
        val truncated: Boolean,
    )

    fun extractUiXml(output: String): String {
        val start = output.indexOf("<?xml").takeIf { it >= 0 } ?: output.indexOf("<hierarchy")
        val end = output.lastIndexOf("</hierarchy>")
        if (start < 0 || end < 0) {
            throw ParseError("UI_DUMP_UNAVAILABLE", "no hierarchy in uiautomator output")
        }
        return output.substring(start, end + "</hierarchy>".length)
    }

    fun parseUiDump(xmlText: String, maxNodes: Int = 200): UiDump {
        val root = try {
            val factory = DocumentBuilderFactory.newInstance()
            factory.setFeature("http://apache.org/xml/features/nonvalidating/load-external-dtd", false)
            factory.setFeature("http://xml.org/sax/features/external-general-entities", false)
            factory.setFeature("http://xml.org/sax/features/external-parameter-entities", false)
            factory.newDocumentBuilder()
                .parse(ByteArrayInputStream(xmlText.toByteArray(Charsets.UTF_8)))
                .documentElement
        } catch (exc: Exception) {
            throw ParseError("UI_DUMP_UNPARSEABLE", exc.message ?: "unparseable hierarchy")
        }
        var screenWidth = 0
        var screenHeight = 0
        if (root.tagName == "hierarchy") {
            // Real dumps carry screen size on the root node, not the tag; skip
            // whitespace text nodes between the tags (unlike Python's find()).
            var child: org.w3c.dom.Node? = root.firstChild
            var firstElement: Element? = null
            while (child != null && firstElement == null) {
                if (child is Element) firstElement = child
                child = child.nextSibling
            }
            val bounds = BOUNDS_RE.find(root.getAttribute("bounds"))
                ?: firstElement?.getAttribute("bounds")?.let { BOUNDS_RE.find(it) }
            if (bounds != null) {
                screenWidth = bounds.groupValues[3].toInt()
                screenHeight = bounds.groupValues[4].toInt()
            }
        }
        val nodes = ArrayList<JsonObject>(maxNodes.coerceAtMost(256))
        var truncated = false
        // Pre-order document-order walk, same as the probe.
        fun visit(element: Element, depth: Int) {
            if (truncated) return
            if (element !== root) {
                if (nodes.size >= maxNodes) {
                    truncated = true
                    return
                }
                val b = BOUNDS_RE.find(element.getAttribute("bounds"))
                nodes.add(
                    buildJsonObject {
                        put("index", nodes.size)
                        put("depth", depth)
                        put("class", element.getAttribute("class"))
                        put("resource_id", element.getAttribute("resource-id"))
                        put("package", element.getAttribute("package"))
                        put("text", element.getAttribute("text").take(MAX_FIELD_CHARS))
                        put("content_desc", element.getAttribute("content-desc").take(MAX_FIELD_CHARS))
                        put("clickable", element.getAttribute("clickable") == "true")
                        put("scrollable", element.getAttribute("scrollable") == "true")
                        if (b != null) {
                            put("bounds", kotlinx.serialization.json.JsonArray(
                                b.groupValues.drop(1).map { v ->
                                    kotlinx.serialization.json.JsonPrimitive(v.toInt())
                                }
                            ))
                        } else {
                            put("bounds", kotlinx.serialization.json.JsonNull)
                        }
                    }
                )
            }
            var child = element.firstChild
            while (child != null) {
                (child as? Element)?.let { visit(it, depth + 1) }
                child = child.nextSibling
            }
        }
        visit(root, 0)
        return UiDump(screenWidth, screenHeight, nodes, truncated)
    }

    fun parseScreenState(dumpsysPower: String): Pair<String, Boolean> {
        val match = WAKEFULNESS_RE.find(dumpsysPower)
            ?: throw ParseError("SCREEN_STATE_UNAVAILABLE", "mWakefulness missing")
        val wakefulness = match.groupValues[1].lowercase()
        return wakefulness to (wakefulness == "awake")
    }

    fun parseCurrentApp(dumpsysWindow: String): Triple<String, String, String> {
        for (line in dumpsysWindow.lineSequence()) {
            if ("mCurrentFocus" in line || "mFocusedApp" in line) {
                val match = COMPONENT_RE.find(line) ?: continue
                val component = match.groupValues[1]
                val pkg = component.substringBefore("/")
                val rest = component.substringAfter("/", "")
                val activity = if (rest.startsWith(".")) pkg + rest else rest
                return Triple(pkg, activity, component)
            }
        }
        throw ParseError("CURRENT_APP_UNAVAILABLE", "no focused window")
    }
}
