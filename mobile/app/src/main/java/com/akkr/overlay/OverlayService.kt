package com.akkr.overlay

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.graphics.RectF
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.Image
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import android.view.WindowManager
import kotlin.math.abs

/**
 * 화면을 캡처해 중국어를 읽고(ML Kit), 사전에서 찾아 번역 상자를 그린다.
 *
 * 안드로이드는 오버레이 창을 화면 캡처에서 뺄 수 없다. 상자를 숨겼다 읽으면 깜빡이므로 숨기지 않는다:
 *  - 상자를 띄운 채로 읽고, 상자가 덮은 자리에서 읽힌 글자는 버린다.
 *  - 상자는 80% 불투명이라 아래 화면이 비친다. 상자 자리의 캡처가 처음과 달라지면 아래 글자가 바뀐 것이므로
 *    그 상자만 치우고, 드러난 글자를 다음 차례에 읽는다.
 *  - 상자 밖은 화면이 멈췄을 때(대사가 다 나온 뒤 등) 읽는다. 늘 움직이는 칸(라이브2D 등)은 변화로 치지 않는다.
 */
class OverlayService : Service() {

    companion object {
        const val EXTRA_CODE = "code"
        const val EXTRA_DATA = "data"
        const val ACTION_STOP = "stop"
        private const val TAG = "AkkrOverlay"
        private const val CHANNEL = "overlay"
        private const val INTERVAL_MS = 300L       // 화면 확인 간격
        private const val FORCE_REFRESH_MS = 3000L // 바뀐 게 없어 보여도 이 시간마다 다시 읽음 (숨기지 않으니 깜빡이지 않음)
        private const val MAX_WAIT_MS = 1500L      // 화면이 계속 움직여도 이만큼 기다렸으면 읽음
        private const val SETTLE_MS = 300L         // 상자를 띄우고 이만큼 지난 캡처를 그 상자의 기준으로 삼음
        private const val MOTION_ALPHA = 0.1f      // 칸별 '자주 움직임' 평균의 갱신 비율
        private const val MOTION_IGNORE = 0.35f    // 이보다 자주 움직이는 칸은 화면 변화로 치지 않음
        private const val GW = Frames.GW
        private const val GH = Frames.GH
        @Volatile var running = false
    }

    /** 띄운 번역 상자. ref = 상자가 그려진 뒤 그 자리의 캡처(기준), limit = 바뀜 판정 배수. */
    private class Shown(val p: Placed, val since: Long, val limit: Float) {
        var ref: FloatArray? = null
    }

    /** 최근에 치운 상자. 같은 글자가 같은 자리에 다시 나오면 움직이는 배경 탓이므로 판정을 느슨하게. */
    private class Gone(val zh: String, val drawn: RectF, val at: Long)

    private lateinit var wm: WindowManager
    private lateinit var view: OverlayView
    private lateinit var matcher: Matcher
    private lateinit var dict: DictStore
    private var projection: MediaProjection? = null
    private var display: VirtualDisplay? = null
    private var reader: ImageReader? = null
    private var capW = 0
    private var capH = 0
    private val worker = HandlerThread("ocr").apply { start() }
    private val bg = Handler(worker.looper)
    private val main = Handler(Looper.getMainLooper())
    private lateinit var pageReader: PageReader
    // 아래는 모두 화면 읽기 스레드(bg)에서만 쓴다
    private var shown: List<Shown> = emptyList()
    private val gone = ArrayDeque<Gone>()
    private var lastFrame: Bitmap? = null
    private var lastThumb: FloatArray? = null
    private var motion = FloatArray(GW * GH)
    private var dirtySince = 0L // 화면이 바뀌기 시작한 때 (0 = 바뀐 것 없음)
    private var lastOcr = 0L

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        // 안드로이드 14부터: 알림이 떠 있는 서비스를 먼저 시작한 뒤에 화면 녹화 권한을 쓸 수 있음
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(1, notification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)
        } else {
            startForeground(1, notification())
        }
        val code = intent?.getIntExtra(EXTRA_CODE, 0) ?: 0
        val data: Intent? = if (Build.VERSION.SDK_INT >= 33) intent?.getParcelableExtra(EXTRA_DATA, Intent::class.java)
        else @Suppress("DEPRECATION") intent?.getParcelableExtra(EXTRA_DATA)
        if (data == null || projection != null) return START_NOT_STICKY

        wm = getSystemService(WINDOW_SERVICE) as WindowManager
        dict = DictStore.open(this)
        matcher = Matcher(dict)
        pageReader = PageReader(matcher, dict.recruit())
        val mpm = getSystemService(MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        projection = mpm.getMediaProjection(code, data).apply {
            registerCallback(object : MediaProjection.Callback() {
                override fun onStop() {
                    stopSelf()
                }
            }, main)
        }
        addOverlay()
        setupCapture()
        running = true
        bg.post(::tick)
        return START_NOT_STICKY
    }

    private fun notification(): Notification {
        val nm = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "번역 오버레이", NotificationManager.IMPORTANCE_LOW))
        val stop = PendingIntent.getService(
            this, 0, Intent(this, OverlayService::class.java).setAction(ACTION_STOP), PendingIntent.FLAG_IMMUTABLE,
        )
        return Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_menu_view)
            .setContentTitle("명방 한글 오버레이 번역 중")
            .setContentText("눌러서 중지")
            .setContentIntent(stop)
            .setOngoing(true)
            .build()
    }

    private fun addOverlay() {
        view = OverlayView(this)
        val lp = WindowManager.LayoutParams(
            WindowManager.LayoutParams.MATCH_PARENT, WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            PixelFormat.TRANSLUCENT,
        )
        // 안드로이드 12부터 다른 앱 창이 덮은 곳의 터치는, 그 창이 80% 이하로 불투명할 때만 아래로 넘어간다
        lp.alpha = 0.8f
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            lp.layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
        }
        wm.addView(view, lp)
    }

    /** 지금 화면 크기(가로/세로 회전 반영)로 캡처를 맞춘다. */
    private fun setupCapture() {
        val (w, h) = screenSize()
        if (w == capW && h == capH && reader != null) return
        capW = w
        capH = h
        val old = reader
        reader = ImageReader.newInstance(capW, capH, PixelFormat.RGBA_8888, 2)
        val dpi = resources.displayMetrics.densityDpi
        if (display == null) {
            display = projection!!.createVirtualDisplay(
                "akkr", capW, capH, dpi, DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR, reader!!.surface, null, bg,
            )
        } else {
            display!!.resize(capW, capH, dpi)
            display!!.surface = reader!!.surface
        }
        old?.close()
        // 화면이 돌면 좌표가 달라지므로 처음부터
        lastThumb = null
        lastFrame = null
        motion = FloatArray(GW * GH)
        if (shown.isNotEmpty()) {
            shown = emptyList()
            publish()
        }
    }

    private fun screenSize(): Pair<Int, Int> {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val b = wm.currentWindowMetrics.bounds
            return b.width() to b.height()
        }
        val dm = android.util.DisplayMetrics()
        @Suppress("DEPRECATION") wm.defaultDisplay.getRealMetrics(dm)
        return dm.widthPixels to dm.heightPixels
    }

    private fun tick() {
        if (!running) return
        try {
            setupCapture()
            step()
        } catch (e: Exception) {
            Log.w(TAG, "tick", e)
        }
        bg.postDelayed(::tick, INTERVAL_MS)
    }

    private fun step() {
        // 화면이 멈춰 있으면 새 캡처가 안 올 수 있으므로 마지막 캡처를 다시 쓴다
        val fresh = grab()
        val frame = fresh ?: lastFrame ?: return
        lastFrame = frame
        val now = SystemClock.uptimeMillis()

        // 1. 상자 아래 글자가 바뀌었으면 그 상자를 치운다 (드러난 글자는 다음 차례에 읽음)
        val keep = ArrayList<Shown>(shown.size)
        val removed = ArrayList<Shown>()
        for (s in shown) {
            val sig = Frames.signature(frame, s.p.drawn)
            val ref = s.ref
            when {
                ref == null -> {
                    // 기준은 상자가 그려진 뒤의 새 캡처로 잡는다 (화면이 오래 멈춰 있으면 마지막 캡처로)
                    if (now - s.since >= SETTLE_MS && (fresh != null || now - s.since >= 2000)) s.ref = sig
                    keep += s
                }
                boxChanged(ref, sig, s.limit) -> removed += s
                else -> keep += s
            }
        }
        if (removed.isNotEmpty()) {
            for (r in removed) gone.addLast(Gone(r.p.item.zh, r.p.drawn, now))
            while (gone.size > 40) gone.removeFirst()
            // 치운 상자와 겹쳐 그려졌던 상자는 캡처가 달라지므로 기준을 다시 잡는다
            for (s in keep) if (removed.any { RectF.intersects(it.p.drawn, s.p.drawn) }) s.ref = null
            shown = keep
            publish()
            if (dirtySince == 0L) dirtySince = now
            lastThumb = null
            Log.d(TAG, "치움 ${removed.size}: ${removed.joinToString { it.p.item.zh.take(12) }}")
            return
        }

        // 2. 상자 밖 화면 변화: 바뀌는 중이면 멈출 때까지 기다린다
        val thumb = Frames.thumbnail(frame)
        val prev = lastThumb
        lastThumb = thumb
        if (prev == null) {
            if (dirtySince == 0L) dirtySince = now
            return
        }
        val moving = changed(prev, thumb, cellMask(shown.map { it.p.drawn }))
        if (moving && dirtySince == 0L) dirtySince = now
        val settled = dirtySince != 0L && (!moving || now - dirtySince >= MAX_WAIT_MS)
        if (!settled && now - lastOcr < FORCE_REFRESH_MS) return

        // 3. 상자를 띄운 채로 읽고, 상자 자리에서 읽힌 글자는 버린다
        dirtySince = 0L
        lastOcr = now
        val covered = shown.map { RectF(it.p.drawn).apply { inset(-4f, -4f) } }
        val items = pageReader.read(frame, covered, shown.map { it.p.item })
        if (items.isEmpty()) return
        val shownAt = SystemClock.uptimeMillis()
        val added = items.map { item ->
            val p = Painter.place(item, capW, screenH = capH)
            // 방금 치운 상자와 같은 글자가 같은 자리에 또 나왔으면 배경이 움직이는 것: 판정을 느슨하게
            val again = gone.count { g -> g.zh == item.zh && now - g.at < 10_000 && RectF.intersects(g.drawn, p.drawn) }
            Shown(p, shownAt, if (again > 0) 3f * again else 1f)
        }
        // 새 상자와 겹쳐 그려지는 기존 상자는 캡처가 달라지므로 기준을 다시 잡는다
        for (s in shown) if (added.any { RectF.intersects(it.p.drawn, s.p.drawn) }) s.ref = null
        shown = shown + added
        publish()
    }

    private fun publish() {
        view.show(shown.map { it.p })
    }

    private fun grab(): Bitmap? {
        val img = reader?.acquireLatestImage() ?: return null
        return img.use { toBitmap(it) }
    }

    private fun toBitmap(img: Image): Bitmap {
        val plane = img.planes[0]
        val rowPad = plane.rowStride - plane.pixelStride * img.width
        val bmp = Bitmap.createBitmap(img.width + rowPad / plane.pixelStride, img.height, Bitmap.Config.ARGB_8888)
        bmp.copyPixelsFromBuffer(plane.buffer)
        return if (rowPad == 0) bmp else Bitmap.createBitmap(bmp, 0, 0, img.width, img.height)
    }

    /** 번역 상자가 걸친 칸 (상자 자체가 바뀌는 것은 화면 변화가 아님). */
    private fun cellMask(rects: List<RectF>): BooleanArray {
        val m = BooleanArray(GW * GH)
        val cw = capW / GW.toFloat()
        val ch = capH / GH.toFloat()
        for (r in rects) {
            val x0 = ((r.left - 4) / cw).toInt().coerceIn(0, GW - 1)
            val x1 = ((r.right + 4) / cw).toInt().coerceIn(0, GW - 1)
            val y0 = ((r.top - 4) / ch).toInt().coerceIn(0, GH - 1)
            val y1 = ((r.bottom + 4) / ch).toInt().coerceIn(0, GH - 1)
            for (y in y0..y1) for (x in x0..x1) m[y * GW + x] = true
        }
        return m
    }

    /** 상자 밖 화면이 바뀌었나. 칸마다 '자주 움직임'을 기억해 늘 움직이는 칸(라이브2D 등)은 빼고 본다. */
    private fun changed(a: FloatArray, b: FloatArray, mask: BooleanArray): Boolean {
        var total = 0f
        var n = 0
        var big = 0
        for (i in a.indices) {
            val d = abs(a[i] - b[i])
            motion[i] = motion[i] * (1 - MOTION_ALPHA) + (if (d > 12) MOTION_ALPHA else 0f)
            if (mask[i] || motion[i] > MOTION_IGNORE) continue
            total += d
            n++
            if (d > 12) big++
        }
        return n > 0 && (total / n > 2f || big > maxOf(2f, n * 0.01f))
    }

    /**
     * 상자 자리의 캡처가 기준과 달라졌나. 상자는 80% 불투명이라 아래 화면 변화가 1/5로 비친다
     * (아래 글자가 바뀌면 칸 평균이 몇~십여 정도 움직임). 상자 자체는 그대로이므로 그 밖의 원인은 거의 없다.
     */
    private fun boxChanged(ref: FloatArray, cur: FloatArray, limit: Float): Boolean {
        if (ref.size != cur.size) return true
        var total = 0f
        var max = 0f
        for (i in ref.indices) {
            val d = abs(ref[i] - cur[i])
            total += d
            if (d > max) max = d
        }
        return total / ref.size > 2.5f * limit || max > 10f * limit
    }

    override fun onDestroy() {
        running = false
        bg.removeCallbacksAndMessages(null)
        runCatching { wm.removeView(view) }
        display?.release()
        reader?.close()
        projection?.stop()
        if (::pageReader.isInitialized) pageReader.close()
        worker.quitSafely()
        if (::dict.isInitialized) dict.close()
        super.onDestroy()
    }
}
