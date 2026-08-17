package dev.porthouse.host.android

import dev.porthouse.host.android.executor.Parsers
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class ParsersTest {

    private val sampleXml = FileResources.read("window_dump.sample.xml")

    @Test
    fun parseUiDump_keepsDocumentOrderAndFlags() {
        val dump = Parsers.parseUiDump(sampleXml)
        assertEquals(1080, dump.screenWidth)
        assertEquals(2400, dump.screenHeight)
        val texts = dump.nodes.map { it["text"]!!.jsonPrimitiveContent() }
        assertEquals(
            listOf(
                "", "", "Settings", "", "Network & internet", "",
                "Connected devices", "About phone",
            ),
            texts,
        )
        val container = dump.nodes[5]
        assertTrue(container["clickable"]!!.jsonPrimitiveContent() == "true")
        assertEquals("com.android.settings:id/container", container["resource_id"]!!.jsonPrimitiveContent())
        assertEquals("3", container["depth"]!!.jsonPrimitiveContent())
        val about = dump.nodes[7]
        assertEquals(
            "[132,2016,948,2100]",
            about["bounds"].toString().replace(Regex("\\s"), ""),
        )
    }

    @Test
    fun parseUiDump_truncatesWithFlag() {
        val dump = Parsers.parseUiDump(sampleXml, maxNodes = 3)
        assertEquals(3, dump.nodes.size)
        assertTrue(dump.truncated)
    }

    @Test
    fun extractUiXml_stripsDeviceNoise() {
        val xml = Parsers.extractUiXml(
            "UI hierchary dumped to: /dev/tty\n" + sampleXml + "\ntrailing noise",
        )
        assertTrue(xml.startsWith("<?xml") || xml.startsWith("<hierarchy"))
        assertTrue(xml.endsWith("</hierarchy>"))
        assertThrows(Parsers.ParseError::class.java) {
            Parsers.extractUiXml("nothing here")
        }
    }

    @Test
    fun parseScreenStateAndCurrentApp() {
        val state = Parsers.parseScreenState("mWakefulness=Awake\nother lines")
        assertEquals("awake" to true, state)
        val app = Parsers.parseCurrentApp(
            "  mCurrentFocus=Window{7f3a u0 com.android.settings/com.android.settings.Settings}",
        )
        assertEquals(
            Triple("com.android.settings", "com.android.settings.Settings", "com.android.settings/com.android.settings.Settings"),
            app,
        )
        assertThrows(Parsers.ParseError::class.java) {
            Parsers.parseCurrentApp("window tree empty")
        }
    }
}

private fun kotlinx.serialization.json.JsonElement.jsonPrimitiveContent(): String =
    (this as kotlinx.serialization.json.JsonPrimitive).content
