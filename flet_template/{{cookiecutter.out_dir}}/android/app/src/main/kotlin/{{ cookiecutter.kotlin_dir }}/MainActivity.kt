package {{ cookiecutter.org_name_2 }}.{{ cookiecutter.package_name }}

import android.app.AlarmManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.media.RingtoneManager
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Log
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import org.json.JSONObject
import java.io.File

/**
 * Pomidor native alarm bridge.
 *
 * Python (embedded) writes a command JSON file; the Dart side (pomidor_bridge.dart)
 * polls it and invokes the "exec" method channel below. Kotlin then:
 *   - schedules an exact system alarm (AlarmManager.setAlarmClock) for the timer
 *     deadline — it fires even if the app process is frozen or killed,
 *   - at fire time AlarmReceiver posts a full-screen-intent notification
 *     ("over everything": lock screen, any app) with sound/vibration per settings,
 *   - plays/stops the in-app alarm sound,
 *   - cancels/dismisses alarms and notifications on pause/reset/skip,
 *   - BootReceiver re-schedules the alarm after device reboot.
 */
class MainActivity : FlutterActivity() {

    companion object {
        const val TAG = "PomidorNative"
        const val METHOD_CHANNEL = "pomidor/native"
        const val NOTIF_ID = 4711
        const val ALARM_PI_REQ = 4712
        const val SHOW_PI_REQ = 4713
        const val PERM_REQ = 4714
        const val MEDIA_CAP_MS = 20000L
    }

    private var media: MediaPlayer? = null
    private val handler = Handler(Looper.getMainLooper())
    private val stopMediaTask = Runnable { stopAlarmSound() }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, METHOD_CHANNEL)
            .setMethodCallHandler { call, result ->
                if (call.method == "exec") {
                    @Suppress("UNCHECKED_CAST")
                    result.success(exec(call.arguments as? Map<Any, Any?>))
                } else {
                    result.notImplemented()
                }
            }
    }

    private fun exec(cfg: Map<Any, Any?>?): Map<String, Any> {
        val res = HashMap<String, Any>()
        var err: String? = null
        try {
            val c: Map<Any, Any?> = cfg ?: emptyMap()
            if (c["perm"] == true) requestNotifPermission()
            if (c["cancel"] == true) cancelAlarm()
            val alarm = c["alarm"] as? Map<*, *>
            if (alarm != null) {
                val ts = (alarm["ts_ms"] as? Number)?.toLong() ?: 0L
                if (ts > System.currentTimeMillis() + 500) {
                    scheduleAlarm(
                        ts,
                        alarm["title"]?.toString() ?: "🍅 Pomidor",
                        alarm["body"]?.toString() ?: "Время вышло!",
                        alarm["sound"] as? Boolean ?: true,
                        alarm["vibro"] as? Boolean ?: true
                    )
                }
            }
            if (c["stop"] == true) stopAlarmSound()
            if (c["play"] == true) {
                val until = (c["until"] as? Number)?.toLong() ?: 0L
                if (until > System.currentTimeMillis()) startAlarmSound()
            }
            if (c["dismiss"] == true) notifyMgr().cancel(NOTIF_ID)
        } catch (e: Exception) {
            err = e.toString()
            Log.e(TAG, "exec failed", e)
        }
        res["notif"] = notifEnabled()
        res["exact"] = canExact()
        if (err != null) res["error"] = err
        return res
    }

    private fun notifyMgr(): NotificationManager =
        getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

    private fun notifEnabled(): Boolean = try {
        notifyMgr().areNotificationsEnabled()
    } catch (e: Exception) {
        false
    }

    private fun canExact(): Boolean = try {
        if (Build.VERSION.SDK_INT >= 31) {
            (getSystemService(Context.ALARM_SERVICE) as AlarmManager).canScheduleExactAlarms()
        } else {
            true
        }
    } catch (e: Exception) {
        false
    }

    private fun requestNotifPermission() {
        try {
            if (Build.VERSION.SDK_INT >= 33) {
                requestPermissions(arrayOf("android.permission.POST_NOTIFICATIONS"), PERM_REQ)
            }
        } catch (e: Exception) {
            Log.e(TAG, "permission request failed", e)
        }
    }

    private fun alarmPendingIntent(title: String, body: String, sound: Boolean, vibro: Boolean): PendingIntent {
        val i = Intent(this, AlarmReceiver::class.java)
            .putExtra("title", title)
            .putExtra("body", body)
            .putExtra("sound", sound)
            .putExtra("vibro", vibro)
        return PendingIntent.getBroadcast(
            this, ALARM_PI_REQ, i,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
    }

    private fun showPendingIntent(): PendingIntent? = try {
        val li = packageManager.getLaunchIntentForPackage(packageName)
        if (li != null) {
            li.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
            PendingIntent.getActivity(
                this, SHOW_PI_REQ, li,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
        } else {
            null
        }
    } catch (e: Exception) {
        null
    }

    private fun scheduleAlarm(ts: Long, title: String, body: String, sound: Boolean, vibro: Boolean) {
        try {
            val am = getSystemService(Context.ALARM_SERVICE) as AlarmManager
            am.setAlarmClock(AlarmManager.AlarmClockInfo(ts, showPendingIntent()),
                alarmPendingIntent(title, body, sound, vibro))
            Log.i(TAG, "alarm scheduled at $ts")
        } catch (e: Exception) {
            Log.e(TAG, "schedule failed", e)
        }
    }

    private fun cancelAlarm() {
        try {
            val am = getSystemService(Context.ALARM_SERVICE) as AlarmManager
            am.cancel(alarmPendingIntent("", "", true, true))
        } catch (e: Exception) {
            Log.e(TAG, "cancel failed", e)
        }
    }

    private fun startAlarmSound() {
        try {
            stopAlarmSound()
            var uri: Uri? = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM)
            if (uri == null) uri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION)
            if (uri == null) uri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE)
            if (uri == null) return
            val mp = MediaPlayer()
            mp.setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ALARM)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                    .build()
            )
            mp.setDataSource(this, uri)
            mp.isLooping = true
            mp.prepare()
            mp.start()
            media = mp
            handler.postDelayed(stopMediaTask, MEDIA_CAP_MS)
        } catch (e: Exception) {
            Log.e(TAG, "alarm sound failed", e)
        }
    }

    private fun stopAlarmSound() {
        try {
            handler.removeCallbacks(stopMediaTask)
            val m = media
            media = null
            if (m != null) {
                if (m.isPlaying) m.stop()
                m.release()
            }
        } catch (e: Exception) {
            Log.e(TAG, "alarm sound stop failed", e)
        }
    }
}

/**
 * Fires the full-screen-intent alarm notification at the timer deadline.
 */
class AlarmReceiver : BroadcastReceiver() {

    companion object {
        const val TAG = "PomidorNative"
        const val NOTIF_ID = 4711
        const val SHOW_PI_REQ = 9001
        val VIB_PATTERN = longArrayOf(0, 500, 250, 500, 250, 500, 700, 500, 250, 500, 250, 500)
    }

    override fun onReceive(context: Context, intent: Intent) {
        try {
            val title = intent.getStringExtra("title") ?: "🍅 Pomidor"
            val body = intent.getStringExtra("body") ?: "Время вышло!"
            val sound = intent.getBooleanExtra("sound", true)
            val vibro = intent.getBooleanExtra("vibro", true)
            val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

            val launch = context.packageManager.getLaunchIntentForPackage(context.packageName)
            if (launch != null) {
                launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
            }
            val fsi: PendingIntent? = if (launch != null) PendingIntent.getActivity(
                context, SHOW_PI_REQ, launch,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            ) else null

            val builder: Notification.Builder
            if (Build.VERSION.SDK_INT >= 26) {
                val chId = "pomidor_" + (if (sound) "sound" else "mute") + "_" +
                        (if (vibro) "vibro" else "still")
                val ch = NotificationChannel(chId, "Pomidor будильник", NotificationManager.IMPORTANCE_HIGH)
                ch.description = "Будильник таймера Pomidor"
                if (sound) {
                    val au = AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_ALARM)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                        .build()
                    ch.setSound(RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM), au)
                } else {
                    ch.setSound(null, null)
                }
                ch.enableVibration(vibro)
                if (vibro) ch.vibrationPattern = VIB_PATTERN
                nm.createNotificationChannel(ch)
                builder = Notification.Builder(context, chId)
            } else {
                builder = Notification.Builder(context)
                builder.setPriority(Notification.PRIORITY_MAX)
                var defaults = 0
                if (sound) defaults = defaults or Notification.DEFAULT_SOUND
                if (vibro) defaults = defaults or Notification.DEFAULT_VIBRATE
                builder.setDefaults(defaults)
            }
            builder.setSmallIcon(context.applicationInfo.icon)
                .setContentTitle(title)
                .setContentText(body)
                .setStyle(Notification.BigTextStyle().bigText(body))
                .setCategory(Notification.CATEGORY_ALARM)
                .setAutoCancel(true)
            if (fsi != null) {
                builder.setFullScreenIntent(fsi, true)
                builder.setContentIntent(fsi)
            }
            nm.notify(NOTIF_ID, builder.build())
            Log.i(TAG, "alarm notification posted")
        } catch (e: Exception) {
            Log.e(TAG, "alarm fire failed", e)
        }
    }
}

/**
 * Re-schedules the alarm after device reboot (alarms do not survive reboot).
 * The timer state JSON is written by the Python side next to the app data.
 */
class BootReceiver : BroadcastReceiver() {

    companion object {
        const val TAG = "PomidorNative"
        const val ALARM_PI_REQ = 4712
        const val SHOW_PI_REQ = 9002
    }

    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action ?: return
        if (action != Intent.ACTION_BOOT_COMPLETED &&
            action != "android.intent.action.QUICKBOOT_POWERON"
        ) return
        try {
            val candidates = listOf(
                File(context.filesDir, "data/pomidor_state.json"),
                File(context.filesDir, "pomidor_state.json")
            )
            var raw: String? = null
            for (f in candidates) {
                if (f.isFile) {
                    raw = f.readText()
                    break
                }
            }
            if (raw.isNullOrEmpty()) return
            val o = JSONObject(raw)
            if (!o.optBoolean("active", false)) return
            val ts = o.optLong("ts_ms", 0L)
            if (ts <= System.currentTimeMillis() + 1000) return
            val pi = PendingIntent.getBroadcast(
                context, ALARM_PI_REQ,
                Intent(context, AlarmReceiver::class.java)
                    .putExtra("title", o.optString("title", "🍅 Pomidor"))
                    .putExtra("body", o.optString("body", "Время вышло!"))
                    .putExtra("sound", o.optBoolean("sound", true))
                    .putExtra("vibro", o.optBoolean("vibro", true)),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
            var show: PendingIntent? = null
            val li = context.packageManager.getLaunchIntentForPackage(context.packageName)
            if (li != null) {
                li.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                show = PendingIntent.getActivity(
                    context, SHOW_PI_REQ, li,
                    PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
                )
            }
            val am = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
            am.setAlarmClock(AlarmManager.AlarmClockInfo(ts, show), pi)
            Log.i(TAG, "alarm re-scheduled after boot at $ts")
        } catch (e: Exception) {
            Log.e(TAG, "boot reschedule failed", e)
        }
    }
}
