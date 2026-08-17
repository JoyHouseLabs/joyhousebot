package dev.porthouse.host.android.runner

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * Pairing material. The jhd_ device token only ever exists in encrypted
 * device storage; the Runtime keeps just its SHA-256 fingerprint.
 */
object DeviceConfig {

    data class Paired(
        val runtimeUrl: String,
        val deviceId: String,
        val token: String,
        val hostRevision: String,
        val hostManifestDigest: String,
    )

    private const val PREFS = "porthouse_device_host"

    fun load(context: Context): Paired? {
        val prefs = prefs(context)
        val url = prefs.getString("runtime_url", null) ?: return null
        val deviceId = prefs.getString("device_id", null) ?: return null
        val token = prefs.getString("token", null) ?: return null
        val revision = prefs.getString("host_revision", null) ?: "android-host@0.1.0"
        val digest = prefs.getString("host_manifest_digest", null) ?: return null
        return Paired(url, deviceId, token, revision, digest)
    }

    fun save(
        context: Context,
        runtimeUrl: String,
        deviceId: String,
        token: String,
        hostRevision: String,
        hostManifestDigest: String,
    ) {
        prefs(context).edit()
            .putString("runtime_url", runtimeUrl.trim())
            .putString("device_id", deviceId.trim())
            .putString("token", token.trim())
            .putString("host_revision", hostRevision.trim())
            .putString("host_manifest_digest", hostManifestDigest.trim())
            .apply()
    }

    fun clear(context: Context) {
        prefs(context).edit().clear().apply()
    }

    private fun prefs(context: Context) = EncryptedSharedPreferences.create(
        context,
        PREFS,
        MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build(),
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )
}
