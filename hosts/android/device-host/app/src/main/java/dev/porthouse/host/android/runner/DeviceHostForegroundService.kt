package dev.porthouse.host.android.runner

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.ServiceInfo
import android.os.IBinder
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import dev.porthouse.host.android.BuildConfig
import dev.porthouse.host.android.R
import dev.porthouse.host.android.executor.AndroidJpegCodec
import dev.porthouse.host.android.executor.DeviceExecutor
import dev.porthouse.host.android.executor.OpSpec
import dev.porthouse.host.android.executor.Parsers
import dev.porthouse.host.android.executor.ShellUserService
import dev.porthouse.host.android.executor.ShizukuDeviceExecutor
import dev.porthouse.host.android.shizuku.IDeviceShellService
import dev.porthouse.host.android.transport.DeviceTransport
import dev.porthouse.host.android.transport.PorthouseTransport
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.serialization.json.buildJsonObject
import rikka.shizuku.Shizuku

/**
 * Foreground service owning the claim loop: heartbeat -> claim -> execute ->
 * complete, with a partial wake lock so leases survive a dark screen. The
 * persistent notification is the user-facing governance surface: it shows
 * pairing state and the last executed operation, with a stop action.
 */
class DeviceHostForegroundService : Service() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var loop: Job? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var boundExecutor: ShizukuDeviceExecutor? = null

    private val userServiceArgs by lazy {
        Shizuku.UserServiceArgs(ComponentName(this, ShellUserService::class.java))
            .processNameSuffix("device_shell")
            .version(1)
            .debuggable(BuildConfig.DEBUG)
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createChannel()
        startForeground(
            NOTIFICATION_ID,
            buildNotification(getString(R.string.status_starting)),
            ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC,
        )
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        if (loop?.isActive == true) return START_STICKY
        val config = DeviceConfig.load(this)
        if (config == null) {
            updateNotification(getString(R.string.status_not_paired))
            stopSelf()
            return START_NOT_STICKY
        }
        wakeLock = (getSystemService(POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "porthouse:device-host")
            .apply { acquire(30 * 60 * 1000L) }
        loop = scope.launch { runLoop(config) }
        return START_STICKY
    }

    private suspend fun runLoop(config: DeviceConfig.Paired) {
        val transport: DeviceTransport = PorthouseTransport(
            baseUrl = config.runtimeUrl,
            deviceId = config.deviceId,
            tokenProvider = { config.token },
        )
        val runner = OperationRunner(
            transport = transport,
            executor = lazyExecutor,
            jpeg = AndroidJpegCodec(),
            screenPrecheck = { screenUsable() },
        )
        var backoffMs = IDLE_POLL_MS
        while (scope.isActive) {
            try {
                transport.heartbeat(config.hostRevision, config.hostManifestDigest)
                val processed = runner.runOnce()
                backoffMs = if (processed > 0) ACTIVE_POLL_MS else IDLE_POLL_MS
                updateNotification(
                    getString(
                        if (processed > 0) R.string.status_last_ops else R.string.status_waiting,
                        processed,
                    ),
                )
                if (processed > 0) continue
            } catch (exc: DeviceTransport.Unauthorized) {
                updateNotification(getString(R.string.status_rejected))
                stopSelf()
                return
            } catch (exc: Exception) {
                backoffMs = (backoffMs * 2).coerceAtMost(MAX_BACKOFF_MS)
                updateNotification(
                    getString(R.string.status_error, (exc.message ?: "error").take(80)),
                )
            }
            delay(backoffMs)
        }
    }

    /** Bind lazily to the Shizuku shell-uid service; fail closed when absent. */
    private val lazyExecutor = object : DeviceExecutor {
        override suspend fun shell(argv: List<String>): String = bind().shell(argv)
        override suspend fun screenshotPng(): ByteArray = bind().screenshotPng()
        override fun isAvailable(): Boolean = runCatching {
            Shizuku.pingBinder()
        }.getOrDefault(false)
    }

    private var serviceConnection: ServiceConnection? = null
    private val bindResult = kotlinx.coroutines.CompletableDeferred<ShizukuDeviceExecutor>()

    /** bindUserService is asynchronous; suspend until the shell binder arrives. */
    private suspend fun bind(): ShizukuDeviceExecutor {
        boundExecutor?.let { return it }
        if (serviceConnection == null) {
            val connection = object : ServiceConnection {
                override fun onServiceConnected(name: ComponentName, service: IBinder) {
                    val executor = ShizukuDeviceExecutor(
                        IDeviceShellService.Stub.asInterface(service),
                    )
                    boundExecutor = executor
                    bindResult.complete(executor)
                }

                override fun onServiceDisconnected(name: ComponentName) {
                    // The completed deferred keeps returning the stale executor;
                    // its calls then fail and the runner reports failed, not hang.
                    boundExecutor = null
                }
            }
            serviceConnection = connection
            Shizuku.bindUserService(userServiceArgs, connection)
        }
        return bindResult.await()
    }

    /** Locked or off screens cannot be observed or actuated reliably. */
    private suspend fun screenUsable(): Boolean = runCatching {
        val output = bind().shell(OpSpec.buildArgv("screen_state", buildJsonObject { }))
        Parsers.parseScreenState(output).second
    }.getOrDefault(false)

    private fun updateNotification(text: String) {
        (getSystemService(NOTIFICATION_SERVICE) as NotificationManager)
            .notify(NOTIFICATION_ID, buildNotification(text))
    }

    private fun buildNotification(text: String): Notification {
        val stopIntent = PendingIntent.getService(
            this,
            1,
            Intent(this, DeviceHostForegroundService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_sys_download_done)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(text)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .addAction(0, getString(R.string.action_stop), stopIntent)
            .build()
    }

    private fun createChannel() {
        (getSystemService(NOTIFICATION_SERVICE) as NotificationManager).createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Device Host", NotificationManager.IMPORTANCE_LOW)
                .apply { description = getString(R.string.channel_description) },
        )
    }

    override fun onDestroy() {
        loop?.cancel()
        scope.cancel()
        serviceConnection?.let { connection ->
            runCatching { Shizuku.unbindUserService(userServiceArgs, connection, true) }
        }
        serviceConnection = null
        boundExecutor = null
        wakeLock?.takeIf { it.isHeld }?.release()
        wakeLock = null
        super.onDestroy()
    }

    companion object {
        private const val CHANNEL_ID = "device_host"
        private const val NOTIFICATION_ID = 1001
        private const val ACTION_STOP = "dev.porthouse.host.android.STOP"
        private const val IDLE_POLL_MS = 2_000L
        private const val ACTIVE_POLL_MS = 200L
        private const val MAX_BACKOFF_MS = 60_000L

        fun start(context: Context) {
            context.startForegroundService(
                Intent(context, DeviceHostForegroundService::class.java),
            )
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, DeviceHostForegroundService::class.java))
        }
    }
}
