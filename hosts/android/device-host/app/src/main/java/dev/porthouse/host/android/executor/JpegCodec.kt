package dev.porthouse.host.android.executor

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import java.io.ByteArrayOutputStream

/**
 * Recode a screencap PNG as JPEG within the inline-result budget. The device
 * complete endpoint caps results at 4 MiB; base64 inflates by 4/3, so the
 * runner passes a pre-base64 budget and this codec degrades quality, then
 * scales, until the payload fits.
 */
interface JpegCodec {
    fun encodePngToJpeg(png: ByteArray, maxBytes: Int): ByteArray
}

class AndroidJpegCodec : JpegCodec {
    override fun encodePngToJpeg(png: ByteArray, maxBytes: Int): ByteArray {
        val bitmap = BitmapFactory.decodeByteArray(png, 0, png.size)
            ?: throw Parsers.ParseError("SCREENSHOT_UNAVAILABLE", "screencap returned no image")
        var quality = 70
        var scale = 1.0f
        while (true) {
            val scaled = if (scale >= 1.0f) bitmap else {
                Bitmap.createScaledBitmap(
                    bitmap,
                    (bitmap.width * scale).toInt().coerceAtLeast(1),
                    (bitmap.height * scale).toInt().coerceAtLeast(1),
                    true,
                )
            }
            val out = ByteArrayOutputStream()
            scaled.compress(Bitmap.CompressFormat.JPEG, quality, out)
            val bytes = out.toByteArray()
            if (bytes.size <= maxBytes) return bytes
            when {
                quality > 40 -> quality -= 15
                scale > 0.4f -> scale *= 0.7f
                else -> return bytes // last resort; runner fails with payload too large
            }
        }
    }
}
