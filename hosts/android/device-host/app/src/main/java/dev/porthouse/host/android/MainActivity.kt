package dev.porthouse.host.android

import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import dev.porthouse.host.android.runner.DeviceConfig
import dev.porthouse.host.android.runner.DeviceHostForegroundService

/**
 * Pairing screen. Register the device in the Porthouse Console
 * (POST /v1/device-hosts), then paste the one-time jhd_ token here along with
 * the Runtime base URL, device id and the registered host revision/digest.
 */
class MainActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        val url = findViewById<EditText>(R.id.runtime_url)
        val deviceId = findViewById<EditText>(R.id.device_id)
        val token = findViewById<EditText>(R.id.device_token)
        val revision = findViewById<EditText>(R.id.host_revision)
        val digest = findViewById<EditText>(R.id.host_manifest_digest)
        val status = findViewById<TextView>(R.id.status)

        DeviceConfig.load(this)?.let {
            url.setText(it.runtimeUrl)
            deviceId.setText(it.deviceId)
            token.setText(it.token)
            revision.setText(it.hostRevision)
            digest.setText(it.hostManifestDigest)
            status.text = getString(R.string.status_paired)
        }

        findViewById<Button>(R.id.start).setOnClickListener {
            val fields = listOf(url, deviceId, token, revision, digest)
            if (fields.any { it.text.toString().isBlank() }) {
                Toast.makeText(this, R.string.toast_fill_all, Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            DeviceConfig.save(
                this,
                url.text.toString(),
                deviceId.text.toString(),
                token.text.toString(),
                revision.text.toString(),
                digest.text.toString(),
            )
            DeviceHostForegroundService.start(this)
            status.text = getString(R.string.status_started)
        }

        findViewById<Button>(R.id.stop).setOnClickListener {
            DeviceHostForegroundService.stop(this)
            status.text = getString(R.string.status_stopped)
        }
    }
}
