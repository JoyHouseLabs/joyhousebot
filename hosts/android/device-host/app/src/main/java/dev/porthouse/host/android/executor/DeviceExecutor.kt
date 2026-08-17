package dev.porthouse.host.android.executor

/** Fixed-argv execution surface; fake in unit tests, Shizuku on device. */
interface DeviceExecutor {
    class ExecError(val exitCode: Int, message: String) : Exception(message)

    /** Run one fixed argv template from OpSpec and return stdout. */
    suspend fun shell(argv: List<String>): String

    /** Capture the screen as PNG bytes via screencap. */
    suspend fun screenshotPng(): ByteArray

    /** Shizuku availability; when false the runner reports manual_required. */
    fun isAvailable(): Boolean
}
