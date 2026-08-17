package dev.porthouse.host.android.executor

import dev.porthouse.host.android.shizuku.IDeviceShellService
import java.io.ByteArrayOutputStream
import kotlinx.coroutines.runBlocking

/**
 * Runs inside the Shizuku UserService process at shell (uid 2000) privilege.
 * It only accepts argv lists produced by OpSpec; there is no free-form
 * command entry point by design (see docs/ANDROID_DEVICE_HOST.md).
 */
class ShellUserService : IDeviceShellService.Stub() {

    override fun exec(argv: Array<out String>): String {
        if (argv.isEmpty()) {
            throw IllegalStateException("empty argv")
        }
        val process = ProcessBuilder(*argv)
            .redirectErrorStream(false)
            .start()
        val stdout = process.inputStream.readBytes().toString(Charsets.UTF_8)
        val stderr = process.errorStream.readBytes().toString(Charsets.UTF_8).take(200)
        val exitCode = runCatching { process.waitFor() }.getOrDefault(-1)
        if (exitCode != 0) {
            throw IllegalStateException("exit=$exitCode stderr=$stderr")
        }
        return stdout
    }

    override fun screenshot(): ByteArray {
        val process = ProcessBuilder("screencap", "-p").redirectErrorStream(false).start()
        val stdout = ByteArrayOutputStream()
        process.inputStream.use { input -> input.copyTo(stdout) }
        val stderr = process.errorStream.readBytes().toString(Charsets.UTF_8).take(200)
        val exitCode = runCatching { process.waitFor() }.getOrDefault(-1)
        if (exitCode != 0) {
            throw IllegalStateException("exit=$exitCode stderr=$stderr")
        }
        return stdout.toByteArray()
    }
}

/** Bind handle that keeps the shell-uid service alive while claimed work runs. */
class ShizukuDeviceExecutor(private val binder: IDeviceShellService) : DeviceExecutor {
    override suspend fun shell(argv: List<String>): String = runBlocking {
        binder.exec(argv.toTypedArray())
    }

    override suspend fun screenshotPng(): ByteArray = runBlocking { binder.screenshot() }

    override fun isAvailable(): Boolean = binder.asBinder().isBinderAlive
}
