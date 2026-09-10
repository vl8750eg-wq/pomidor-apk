package {{ cookiecutter.org_name_2 }}.{{ cookiecutter.package_name }}

import android.app.AlarmManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.media.RingtoneManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.VibrationEffect
import android.os.Vibrator
import android.provider.Settings
import android.util.Log
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import io.flutter.embedding.android.FlutterActivity
import org.json.JSONObject
import java.io.File

/**
 * Pomidor native alarm bridge, v1.0.4 (Kotlin-centric).
 *
 * Design goals (learned from v1.0.3 field failure):
 *   - The critical path must NOT depend on Dart. The embedded Python writes a
 *     full state snapshot to `pomidor_state.json`; MainActivity itself polls
 *     that file every 700 ms while the UI is visible and arms/cancels the
 *     system alarm. If any layer dies, arming already happened in foreground.
 *   - The deadline alert is delivered by AlarmManager.setAlarmClock launching
 *     a dedicated AlarmActivity. setAlarmClock is exact, fires even in Doze
 *     and after process death, needs no special permission, and launching the
 *     alarm-clock operation intent is exempt from background-activity-launch
 *     restrictions - the activity covers the lock screen and any other app.
 *     This works even when notifications are blocked (Android 13+ runtime
 *     permission, MIUI auto-denial, etc.).
 *   - Auto-started phases (break/focus chains) are pre-computed by Python in
 *     the "next" field; AlarmActivity re-arms the next alarm natively, so a
 *     whole pomodoro session beeps on schedule even with Python dead.
 *   - POST_NOTIFICATIONS is requested directly at startup, and the resulting
 *     status is written to `pomidor_ack.json` so the Python UI can show a
 *     warning banner and offer a 15-second self-test alarm.
 */
class MainActivity : FlutterActivity() {

    companion object {
        const val TAG = "PomidorNative"
        const val ALARM_REQ = 4712
        const val TEST_REQ = 4715
        const val PERM_REQ = 4714
        const val POLL_MS = 700L
        const val REASSERT_TICKS = 7

        // True while the Flutter UI is on screen; AlarmActivity skips the
        // native takeover in that case (Python shows its own overlay).
        @Volatile
        var uiResumed = false
    }

    private val handler = Handler(Looper.getMainLooper())
    private var lastGen = ""
    private var lastAct = ""
    private var ticks = 0
    private var media: MediaPlayer? = null
    private val stopSoundTask = Runnable { stopAlarmSound() }

    private val poll = object : Runnable {
        override fun run() {
            ticks++
            try {
                syncState(ticks % REASSERT_TICKS == 0)
            } catch (e: Exception) {
                Log.e(TAG, "syncState failed", e)
            }
            handler.postDelayed(this, POLL_MS)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        requestNotifPermission()
    }

    override fun onResume() {
        super.onResume()
        uiResumed = true
        handler.removeCallbacks(poll)
        handler.post(poll)
    }

    override fun onPause() {
        uiResumed = false
        handler.removeCallbacks(poll)
        // One last sync right before the UI goes away: covers "started the
        // timer and immediately switched apps".
        try {
            syncState(false)
        } catch (_: Exception) {
        }
        super.onPause()
    }

    private fun alarmMgr(): AlarmManager =
        getSystemService(Context.ALARM_SERVICE) as AlarmManager

    private fun notifMgr(): NotificationManager =
        getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

    private fun notifEnabled(): Boolean = try {
        notifMgr().areNotificationsEnabled()
    } catch (e: Exception) {
        false
    }

    private fun canExact(): Boolean = try {
        if (Build.VERSION.SDK_INT >= 31) alarmMgr().canScheduleExactAlarms() else true
    } catch (e: Exception) {
        false
    }

    private fun requestNotifPermission() {
        try {
            if (Build.VERSION.SDK_INT >= 33 &&
                checkSelfPermission("android.permission.POST_NOTIFICATIONS") !=
                PackageManager.PERMISSION_GRANTED
            ) {
                requestPermissions(arrayOf("android.permission.POST_NOTIFICATIONS"), PERM_REQ)
            }
        } catch (e: Exception) {
            Log.e(TAG, "permission request failed", e)
        }
    }

    private fun openNotifSettings() {
        try {
            if (Build.VERSION.SDK_INT >= 26) {
                startActivity(
                    Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                        .putExtra(Settings.EXTRA_APP_PACKAGE, packageName)
                )
            } else {
                throw Exception("fallback")
            }
        } catch (e: Exception) {
            try {
                startActivity(
                    Intent(
                        Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                        Uri.fromParts("package", packageName, null)
                    )
                )
            } catch (_: Exception) {
            }
        }
    }

    /**
     * Reads the state snapshot written by Python and reconciles it with the
     * system alarm. Every state carries a unique `gen`; only changes are
     * acted upon (plus a periodic re-assert to self-heal external cancels).
     */
    private fun syncState(reassert: Boolean) {
        val o = PomidorState.read(this) ?: return
        val gen = o.optString("gen", "")
        if (gen.isEmpty()) return
        val changed = gen != lastGen
        val active = o.optBoolean("active", false)
        val ts = o.optLong("ts_ms", 0L)
        if (active && ts > System.currentTimeMillis() + 600) {
            if (changed || reassert) {
                PomidorAlarm.schedule(
                    this, ALARM_REQ, ts,
                    o.optString("title", "🍅 Pomidor"),
                    o.optString("body", "Время вышло!"),
                    o.optBoolean("sound", true),
                    o.optBoolean("vibro", true),
                    false
                )
                if (changed) {
                    PomidorState.writeAck(this, gen, true, ts, notifEnabled(), canExact(), "", "")
                }
            }
        } else if (changed) {
            // Disarmed, or the deadline already passed (stale state from a
            // dead process) - drop whatever we hold.
            PomidorAlarm.cancel(this, ALARM_REQ)
            PomidorState.writeAck(this, gen, true, 0, notifEnabled(), canExact(), "", "")
        }
        lastGen = gen

        val act = o.optString("act", "")
        if (act.isNotEmpty() && act != lastAct) {
            lastAct = act
            when (act) {
                "test" -> {
                    val at = System.currentTimeMillis() + 15000
                    PomidorAlarm.schedule(
                        this, TEST_REQ, at,
                        "🔔 ТЕСТ БУДИЛЬНИКА POMIDOR",
                        "Если ты это видишь со звуком и вибрацией - всё работает!",
                        o.optBoolean("sound", true),
                        o.optBoolean("vibro", true),
                        true
                    )
                    PomidorState.writeAck(this, gen, true, at, notifEnabled(), canExact(), "test", "")
                }
                "settings" -> {
                    openNotifSettings()
                    val cur = if (active && ts > System.currentTimeMillis()) ts else 0L
                    PomidorState.writeAck(this, gen, true, cur, notifEnabled(), canExact(), "settings", "")
                }
                "play" -> {
                    val until = o.optLong("act_until", 0L)
                    if (until > System.currentTimeMillis()) startAlarmSound()
                }
                "stop" -> stopAlarmSound()
            }
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
            handler.postDelayed(stopSoundTask, 90000)
        } catch (e: Exception) {
            Log.e(TAG, "alarm sound failed", e)
        }
    }

    private fun stopAlarmSound() {
        try {
            handler.removeCallbacks(stopSoundTask)
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

/** Helpers shared by MainActivity / AlarmActivity / BootReceiver. */
object PomidorAlarm {

    const val TAG = "PomidorNative"

    fun opIntent(
        c: Context, req: Int, title: String, body: String,
        sound: Boolean, vibro: Boolean, test: Boolean
    ): PendingIntent {
        val i = Intent(c, AlarmActivity::class.java)
            .putExtra("title", title)
            .putExtra("body", body)
            .putExtra("sound", sound)
            .putExtra("vibro", vibro)
            .putExtra("test", test)
        return PendingIntent.getActivity(
            c, req, i,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
    }

    fun showIntent(c: Context, req: Int): PendingIntent? = try {
        val li = c.packageManager.getLaunchIntentForPackage(c.packageName) ?: return null
        li.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        PendingIntent.getActivity(
            c, req, li,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
    } catch (e: Exception) {
        null
    }

    fun schedule(
        c: Context, req: Int, ts: Long, title: String, body: String,
        sound: Boolean, vibro: Boolean, test: Boolean
    ) {
        try {
            val am = c.getSystemService(Context.ALARM_SERVICE) as AlarmManager
            am.setAlarmClock(
                AlarmManager.AlarmClockInfo(ts, showIntent(c, 4713)),
                opIntent(c, req, title, body, sound, vibro, test)
            )
            Log.i(TAG, "alarm req=" + req + " scheduled at " + ts)
        } catch (e: Exception) {
            Log.e(TAG, "schedule failed", e)
        }
    }

    fun cancel(c: Context, req: Int) {
        try {
            val am = c.getSystemService(Context.ALARM_SERVICE) as AlarmManager
            am.cancel(opIntent(c, req, "", "", true, true, false))
        } catch (e: Exception) {
            Log.e(TAG, "cancel failed", e)
        }
    }
}

/** Reads the Python-written state snapshot and writes acks. */
object PomidorState {

    fun read(c: Context): JSONObject? {
        val candidates = listOf(
            File(c.filesDir, "data/pomidor_state.json"),
            File(c.filesDir, "pomidor_state.json")
        )
        for (f in candidates) {
            try {
                if (f.isFile) {
                    val raw = f.readText()
                    if (raw.isNotBlank()) {
                        return JSONObject(raw)
                    }
                }
            } catch (_: Exception) {
            }
        }
        return null
    }

    private fun ackDir(c: Context): File =
        if (File(c.filesDir, "data").isDirectory) File(c.filesDir, "data") else c.filesDir

    fun writeAck(
        c: Context, gen: String, ok: Boolean, armed: Long,
        notif: Boolean, exact: Boolean, act: String, error: String
    ) {
        try {
            val o = JSONObject()
            o.put("id", gen)
            o.put("ok", ok)
            o.put("armed", armed)
            o.put("notif", notif)
            o.put("exact", exact)
            if (act.isNotEmpty()) o.put("act", act)
            if (error.isNotEmpty()) o.put("error", error)
            o.put("ts", System.currentTimeMillis())
            File(ackDir(c), "pomidor_ack.json").writeText(o.toString())
        } catch (_: Exception) {
        }
    }
}

/**
 * Full-screen deadline alert. Launched by the system alarm clock operation,
 * so it appears over the lock screen and over any other app, with sound and
 * vibration produced by the activity itself (no notification permission
 * required). Also posts a best-effort notification for the shade, and chains
 * the auto-started next phase from the state snapshot.
 */
class AlarmActivity : android.app.Activity() {

    companion object {
        const val TAG = "PomidorNative"
        const val NOTIF_ID = 4711
        val VIB_PATTERN = longArrayOf(0, 500, 250, 500, 250, 500, 700, 500, 250, 500, 250, 500)
    }

    private val handler = Handler(Looper.getMainLooper())
    private var media: MediaPlayer? = null
    private var vibe: Vibrator? = null
    private val stopAllTask = Runnable { stopAlert() }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        try {
            if (Build.VERSION.SDK_INT >= 27) {
                setShowWhenLocked(true)
                setTurnScreenOn(true)
            } else {
                @Suppress("DEPRECATION")
                window.addFlags(
                    WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED
                        or WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON
                )
            }
            window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        } catch (_: Exception) {
        }

        val isTest = intent.getBooleanExtra("test", false)
        // The Flutter UI is already on screen: Python shows its own in-app
        // overlay, so the native takeover is redundant. User-requested tests
        // always run so they can be verified visually.
        if (MainActivity.uiResumed && !isTest) {
            chainNext()
            finish()
            return
        }

        val title = intent.getStringExtra("title") ?: "🍅 Pomidor"
        val body = intent.getStringExtra("body") ?: "Время вышло!"
        val sound = intent.getBooleanExtra("sound", true)
        val vibro = intent.getBooleanExtra("vibro", true)

        buildUi(title, body)
        if (sound) startSound()
        if (vibro) startVibro()
        postNotification(title, body)
        chainNext()
        handler.postDelayed(stopAllTask, 120000)
    }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()

    private fun buildUi(title: String, body: String) {
        val col = LinearLayout(this)
        col.orientation = LinearLayout.VERTICAL
        col.gravity = Gravity.CENTER
        col.setPadding(dp(24), dp(24), dp(24), dp(24))

        val emoji = TextView(this)
        emoji.text = "⏰"
        emoji.textSize = 90f
        emoji.gravity = Gravity.CENTER

        val t = TextView(this)
        t.text = title
        t.textSize = 26f
        t.setTypeface(null, android.graphics.Typeface.BOLD)
        t.setTextColor(0xFFFFFFFF.toInt())
        t.gravity = Gravity.CENTER
        t.setPadding(0, dp(12), 0, dp(6))

        val b = TextView(this)
        b.text = body
        b.textSize = 16f
        b.setTextColor(0xFFE0E0E0.toInt())
        b.gravity = Gravity.CENTER

        val spacer = View(this)
        spacer.layoutParams = LinearLayout.LayoutParams(1, dp(28))

        val btn = Button(this)
        btn.text = "ПОНЯТНО, ПРОДОЛЖИТЬ"
        btn.setOnClickListener {
            stopAlert()
            finish()
        }

        col.addView(emoji)
        col.addView(t)
        col.addView(b)
        col.addView(spacer)
        col.addView(btn)

        val sc = ScrollView(this)
        sc.addView(col)
        sc.setBackgroundColor(0xFFB71C1C.toInt())
        setContentView(sc)
    }

    private fun startSound() {
        try {
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
        } catch (e: Exception) {
            Log.e(TAG, "alarm sound failed", e)
        }
    }

    private fun startVibro() {
        try {
            val v = getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator
            if (v == null || !v.hasVibrator()) return
            vibe = v
            if (Build.VERSION.SDK_INT >= 26) {
                v.vibrate(VibrationEffect.createWaveform(VIB_PATTERN, 0))
            } else {
                @Suppress("DEPRECATION")
                v.vibrate(VIB_PATTERN, 0)
            }
        } catch (e: Exception) {
            Log.e(TAG, "vibro failed", e)
        }
    }

    private fun stopAlert() {
        try {
            handler.removeCallbacks(stopAllTask)
        } catch (_: Exception) {
        }
        try {
            val m = media
            media = null
            if (m != null) {
                if (m.isPlaying) m.stop()
                m.release()
            }
        } catch (_: Exception) {
        }
        try {
            vibe?.cancel()
        } catch (_: Exception) {
        }
        vibe = null
        try {
            (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).cancel(NOTIF_ID)
        } catch (_: Exception) {
        }
    }

    override fun onDestroy() {
        stopAlert()
        super.onDestroy()
    }

    private fun postNotification(title: String, body: String) {
        try {
            val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            if (Build.VERSION.SDK_INT >= 26) {
                val ch = NotificationChannel(
                    "pomidor_alarm", "Pomidor будильник",
                    NotificationManager.IMPORTANCE_HIGH
                )
                ch.description = "Срабатывание таймера Pomidor"
                // Sound and vibration are produced by the activity itself.
                ch.setSound(null, null)
                ch.enableVibration(false)
                nm.createNotificationChannel(ch)
            }
            val builder: Notification.Builder =
                if (Build.VERSION.SDK_INT >= 26) {
                    Notification.Builder(this, "pomidor_alarm")
                } else {
                    Notification.Builder(this).setPriority(Notification.PRIORITY_MAX)
                }
            builder.setSmallIcon(applicationInfo.icon)
                .setContentTitle(title)
                .setContentText(body)
                .setStyle(Notification.BigTextStyle().bigText(body))
                .setCategory(Notification.CATEGORY_ALARM)
                .setAutoCancel(true)
            val show = PomidorAlarm.showIntent(this, 9001)
            if (show != null) builder.setContentIntent(show)
            nm.notify(NOTIF_ID, builder.build())
        } catch (e: Exception) {
            Log.e(TAG, "notification failed", e)
        }
    }

    /** Arms the auto-started next phase so the chain survives process death. */
    private fun chainNext() {
        try {
            val o = PomidorState.read(this) ?: return
            val nx = o.optJSONObject("next") ?: return
            if (!nx.optBoolean("active", false)) return
            val ts = nx.optLong("ts_ms", 0L)
            if (ts <= System.currentTimeMillis() + 1000) return
            PomidorAlarm.schedule(
                this, MainActivity.ALARM_REQ, ts,
                nx.optString("title", "🍅 Pomidor"),
                nx.optString("body", "Время вышло!"),
                nx.optBoolean("sound", true),
                nx.optBoolean("vibro", true),
                false
            )
            Log.i(TAG, "next phase chained at " + ts)
        } catch (e: Exception) {
            Log.e(TAG, "chain failed", e)
        }
    }
}

/**
 * Re-arms the alarm after device reboot (alarms do not survive reboot).
 */
class BootReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action ?: return
        if (action != Intent.ACTION_BOOT_COMPLETED &&
            action != "android.intent.action.QUICKBOOT_POWERON"
        ) return
        try {
            val o = PomidorState.read(context) ?: return
            if (!o.optBoolean("active", false)) return
            val ts = o.optLong("ts_ms", 0L)
            if (ts <= System.currentTimeMillis() + 1000) return
            val pi = PomidorAlarm.opIntent(
                context, MainActivity.ALARM_REQ,
                o.optString("title", "🍅 Pomidor"),
                o.optString("body", "Время вышло!"),
                o.optBoolean("sound", true),
                o.optBoolean("vibro", true),
                false
            )
            val am = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
            am.setAlarmClock(AlarmManager.AlarmClockInfo(ts, PomidorAlarm.showIntent(context, 9002)), pi)
            Log.i(PomidorAlarm.TAG, "alarm re-scheduled after boot at " + ts)
        } catch (e: Exception) {
            Log.e(PomidorAlarm.TAG, "boot reschedule failed", e)
        }
    }
}
