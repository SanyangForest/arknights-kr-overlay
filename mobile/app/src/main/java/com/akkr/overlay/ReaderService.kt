package com.akkr.overlay

import android.accessibilityservice.AccessibilityService
import android.annotation.TargetApi
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.graphics.Rect
import android.graphics.RectF
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.SystemClock
import android.util.Log
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityWindowInfo
import java.util.concurrent.Executor
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

/**
 * 접근성 서비스 방식 (안드로이드 14 이상).
 *
 *  - 번역 상자를 접근성 창에 그린다: 100% 불투명해도 터치가 아래 게임으로 넘어간다 (일반 앱 창은 80%까지만).
 *  - 게임 창만 캡처한다(takeScreenshotOfWindow): 번역 상자가 캡처에 섞이지 않아 상자 아래 원문을 그대로 볼 수 있다.
 *    그래서 상자 아래 글자가 바뀌면 바로 알고, 메뉴판이 기울어 글자가 몇 픽셀 움직이면 상자도 따라 옮긴다.
 *  - 게임 창이 화면에 있을 때만 읽고, 다른 앱으로 가면 상자를 지운다.
 */
@TargetApi(Build.VERSION_CODES.UPSIDE_DOWN_CAKE)
class ReaderService : AccessibilityService() {

    companion object {
        private const val TAG = "AkkrOverlay"
        private const val INTERVAL_MS = 400L       // 게임 창 캡처 간격
        private const val FORCE_REFRESH_MS = 3000L // 바뀐 게 없어 보여도 이 시간마다 다시 읽음
        private const val MAX_WAIT_MS = 1500L      // 화면이 계속 움직여도 이만큼 기다렸으면 읽음
        private const val MOTION_ALPHA = 0.1f      // 칸별 '자주 움직임' 평균의 갱신 비율
        private const val MOTION_IGNORE = 0.35f    // 이보다 자주 움직이는 칸은 화면 변화로 치지 않음
        private const val SHIFT_RANGE = 24         // 상자 아래 글자가 이만큼(픽셀) 안에서 움직였으면 따라 옮김
        const val PREFS = "akkr"
        const val KEY_ON = "translate_on"
        /** 번역할 게임: 중국 서버 (빌리빌리판, 공식판) */
        val GAME_PACKAGES = setOf("com.hypergryph.arknights.bilibili", "com.hypergryph.arknights")
        @Volatile var connected = false
    }

    /** 띄운 번역 상자. sig·patch = 처음 읽을 때 원문 자리의 모습 (바뀌었는지, 움직였는지 볼 기준). */
    /** loose: 같은 자리에서 지웠다 다시 읽은 횟수. 빛나는 버튼처럼 늘 깜빡이는 글이면 그만큼 느슨하게 본다. */
    private class Shown(var p: Placed, var sig: FloatArray, var patch: Patch, val loose: Int = 0)

    /** 최근에 치운 상자 (같은 글이 같은 자리에 다시 나오면 깜빡이는 글) */
    private class Gone(val zh: String, val box: RectF, val at: Long)

    private lateinit var wm: WindowManager
    private var view: OverlayView? = null
    private var worker: HandlerThread? = null
    private lateinit var bg: Handler
    private var dict: DictStore? = null
    private var reader: PageReader? = null
    // 아래는 화면 읽기 스레드(bg)에서만 쓴다
    private var shown: List<Shown> = emptyList()
    private val gone = ArrayDeque<Gone>()
    private var lastThumb: FloatArray? = null
    private var motion = FloatArray(Frames.GW * Frames.GH)
    private var dirtySince = 0L // 화면이 바뀌기 시작한 때 (0 = 바뀐 것 없음)
    private var lastOcr = 0L
    private val frame = Rect() // 캡처한 게임 창의 화면 위 자리
    @Volatile private var running = false

    override fun onServiceConnected() {
        connected = true
        wm = getSystemService(WINDOW_SERVICE) as WindowManager
        addOverlay()
        val w = HandlerThread("reader").apply { start() }
        worker = w
        bg = Handler(w.looper)
        running = true
        bg.post {
            try {
                val d = DictStore.open(this)
                dict = d
                reader = PageReader(Matcher(d), d.recruit())
            } catch (e: Exception) {
                Log.e(TAG, "사전을 열지 못함", e)
            }
            tick()
        }
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {}

    override fun onInterrupt() {}

    override fun onUnbind(intent: Intent?): Boolean {
        shutdown()
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        shutdown()
        super.onDestroy()
    }

    private fun shutdown() {
        if (!running) return
        running = false
        connected = false
        bg.removeCallbacksAndMessages(null)
        view?.let { v -> runCatching { wm.removeView(v) } }
        view = null
        bg.post {
            reader?.close()
            dict?.close()
            worker?.quitSafely()
        }
    }

    private fun addOverlay() {
        val v = OverlayView(this)
        val lp = WindowManager.LayoutParams(
            WindowManager.LayoutParams.MATCH_PARENT, WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            PixelFormat.TRANSLUCENT,
        )
        lp.layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
        wm.addView(v, lp)
        view = v
    }

    private fun enabled() = getSharedPreferences(PREFS, MODE_PRIVATE).getBoolean(KEY_ON, true)

    private fun next(delay: Long = INTERVAL_MS) {
        if (running) bg.postDelayed(::tick, delay)
    }

    private fun tick() {
        if (!running) return
        val r = reader
        if (r == null || !enabled()) {
            clear()
            next(1000)
            return
        }
        val win = gameWindow()
        if (win == null) {
            clear()
            next(800)
            return
        }
        val bounds = Rect().also { win.getBoundsInScreen(it) }
        takeScreenshotOfWindow(win.id, Executor { bg.post(it) }, object : TakeScreenshotCallback {
            override fun onSuccess(result: ScreenshotResult) {
                try {
                    step(toBitmap(result), bounds, r)
                } catch (e: Exception) {
                    Log.w(TAG, "step", e)
                }
                next()
            }

            override fun onFailure(errorCode: Int) {
                Log.d(TAG, "캡처 실패 $errorCode")
                next(if (errorCode == ERROR_TAKE_SCREENSHOT_INTERVAL_TIME_SHORT) 200 else 1000)
            }
        })
    }

    /** 화면에 떠 있는 게임 창 (다른 앱으로 가면 목록에서 빠짐). */
    private fun gameWindow(): AccessibilityWindowInfo? = windows.firstOrNull { w ->
        w.type == AccessibilityWindowInfo.TYPE_APPLICATION && (w.root?.packageName?.toString() ?: "") in GAME_PACKAGES
    }

    private fun toBitmap(result: ScreenshotResult): Bitmap {
        val hb = result.hardwareBuffer
        try {
            val hw = Bitmap.wrapHardwareBuffer(hb, result.colorSpace) ?: error("캡처를 그림으로 바꾸지 못함")
            return hw.copy(Bitmap.Config.ARGB_8888, false)
        } finally {
            hb.close()
        }
    }

    private fun clear() {
        if (shown.isEmpty() && lastThumb == null) return
        shown = emptyList()
        lastThumb = null
        dirtySince = 0L
        publish()
    }

    private fun publish() {
        view?.show(shown.map { it.p }, frame.left.toFloat(), frame.top.toFloat())
    }

    private fun step(bmp: Bitmap, bounds: Rect, r: PageReader) {
        val now = SystemClock.uptimeMillis()
        if (bounds != frame) {
            // 게임 창 크기·자리가 바뀜 (화면 회전 등): 처음부터
            frame.set(bounds)
            shown = emptyList()
            lastThumb = null
            motion = FloatArray(Frames.GW * Frames.GH)
            publish()
        }

        // 1. 상자 아래 원문이 그대로인가. 몇 픽셀 움직였으면 따라 옮기고, 바뀌었으면 치운다
        var changedBoxes = false
        val keep = ArrayList<Shown>(shown.size)
        for (s in shown) {
            if (!differs(s.sig, Frames.signature(bmp, s.p.item.box), s.loose)) {
                keep += s
                continue
            }
            // 칸 평균은 달라졌어도 글자 획이 제자리에 그대로면 (반투명 막대 뒤로 전투가 움직임) 그대로 둔다
            if (Frames.findShift(bmp, s.patch, 0, offFrac = offFrac(s.loose)) == Pair(0, 0)) {
                keep += s
                continue
            }
            changedBoxes = true
            val shift = Frames.findShift(bmp, s.patch, SHIFT_RANGE)
            if (shift != null && (shift.first != 0 || shift.second != 0)) {
                s.p = s.p.moved(shift.first.toFloat(), shift.second.toFloat())
                s.sig = Frames.signature(bmp, s.p.item.box)
                s.patch = Frames.patch(bmp, s.p.item.box)
                keep += s
            } else if (shift == null) {
                if (dirtySince == 0L) dirtySince = now
                gone.addLast(Gone(s.p.item.zh, RectF(s.p.item.box), now))
                while (gone.size > 40) gone.removeFirst()
                Log.d(TAG, "치움: ${s.p.item.zh.take(12)}")
            } else {
                // 같은 자리에서 맞는데 서명만 달라짐 (깜빡이는 효과 등): 기준만 새로
                s.sig = Frames.signature(bmp, s.p.item.box)
                keep += s
            }
        }
        if (changedBoxes) {
            shown = keep
            publish()
        }

        // 2. 화면 변화: 바뀌는 중이면 멈출 때까지 기다린다 (늘 움직이는 칸은 빼고 봄)
        val thumb = Frames.thumbnail(bmp)
        val prev = lastThumb
        lastThumb = thumb
        // 화면 전환(홈 → 공개모집 등): 띄워 둔 상자를 모두 치운다. 글자 그림 비교만으로는 밝은 바탕의 작은 글('角色管理')이
        // 새 화면의 비슷한 자리와 맞다고 나와 옛 상자가 남았음 (PC판 SCENE_FRACTION과 같은 기준)
        if (prev != null && shown.isNotEmpty() && sceneChanged(prev, thumb)) {
            Log.d(TAG, "화면 전환: 상자 ${shown.size}개 치움")
            shown = emptyList()
            publish()
        }
        val moving = prev == null || changed(prev, thumb)
        if (moving && dirtySince == 0L) dirtySince = now
        val settled = dirtySince != 0L && (!moving || now - dirtySince >= MAX_WAIT_MS)
        if (!settled && now - lastOcr < FORCE_REFRESH_MS) return

        // 3. 읽고, 이미 띄운 상자와 맞춰 본다
        dirtySince = 0L
        lastOcr = now
        val items = r.read(bmp, olds = shown.map { it.p.item })
        reconcile(bmp, items)
    }

    /** 새로 읽은 상자와 띄워 둔 상자 합치기: 같은 글이 같은 자리면 그대로 두어 흔들리지 않게. */
    private fun reconcile(bmp: Bitmap, items: List<Item>) {
        val result = ArrayList<Shown>()
        val used = HashSet<Shown>()
        var changedAny = false
        for (it in items) {
            val same = shown.firstOrNull { s -> s !in used && s.p.item.zh == it.zh && overlap(s.p.item.box, it.box) >= 0.5f }
            if (same != null) {
                used += same
                result += same
                continue
            }
            // 이번에 읽은 다른 글자와 이미 띄워 둔 상자 모두 (번역문이 넓어져 이웃을 덮지 않게)
            val others = items.filter { o -> o !== it }.map { o -> o.box } + shown.map { o -> o.p.item.box }
            val now = SystemClock.uptimeMillis()
            val again = gone.count { g -> g.zh == it.zh && now - g.at < 20_000 && RectF.intersects(g.box, it.box) }
            result += Shown(Painter.place(it, bmp.width, others, bmp.height), Frames.signature(bmp, it.box), Frames.patch(bmp, it.box), again)
            changedAny = true
        }
        // 이번에 못 읽은 상자: 그 자리 원문이 그대로이고 새 상자와 겹치지 않으면 남긴다 (OCR이 가끔 놓침)
        for (s in shown) {
            if (s in used) continue
            // 같은 글이 다른 자리에 새로 읽혔으면 옮겨 간 것 (흘러가는 안내 문구): 옛 상자는 지움
            // 같은 문장이 여러 칸에 있으면(공개모집 칸마다 '已找到符合…') 이번에 읽힌 수가 띄워 둔 수보다 적을 때는 옮긴 게 아님
            val moved = s.p.item.zh.length > 4 && items.any { it.zh == s.p.item.zh } &&
                shown.count { it.p.item.zh == s.p.item.zh } <= items.count { it.zh == s.p.item.zh }
            val keep = !moved && result.none { RectF.intersects(it.p.item.box, s.p.item.box) } &&
                Frames.findShift(bmp, s.patch, 0, offFrac = offFrac(s.loose)) != null
            if (keep) result += s else changedAny = true
        }
        if (changedAny || result.size != shown.size) {
            shown = result
            publish()
        }
    }

    /** 두 상자가 겹친 넓이 / 작은 쪽 넓이 */
    private fun overlap(a: RectF, b: RectF): Float {
        val w = min(a.right, b.right) - max(a.left, b.left)
        val h = min(a.bottom, b.bottom) - max(a.top, b.top)
        if (w <= 0 || h <= 0) return 0f
        return w * h / max(1f, min(a.width() * a.height(), b.width() * b.height()))
    }

    /** 원문 자리의 모습이 달라졌나 (캡처에 상자가 없으므로 원문 변화가 그대로 보임). */
    private fun differs(a: FloatArray, b: FloatArray, loose: Int = 0): Boolean {
        val (mean, top) = Frames.sigDiff(a, b)
        val k = 1f + loose
        return mean > 6f * k || top > 28f * k
    }

    /** 글자 획 중 바뀌어도 되는 비율 (깜빡이는 글일수록 크게) */
    private fun offFrac(loose: Int) = minOf(0.2f + 0.15f * loose, 0.6f)

    /** 화면이 바뀌었나. 칸마다 '자주 움직임'을 기억해 늘 움직이는 칸(라이브2D 등)은 빼고 본다. */
    /** 화면 대부분(60% 넘게)이 크게 바뀌었나 (늘 움직이는 칸도 포함해서 잼). */
    private fun sceneChanged(a: FloatArray, b: FloatArray): Boolean =
        a.indices.count { abs(a[it] - b[it]) > 25f } > a.size * 0.6f

    private fun changed(a: FloatArray, b: FloatArray): Boolean {
        var total = 0f
        var n = 0
        var big = 0
        for (i in a.indices) {
            val d = abs(a[i] - b[i])
            motion[i] = motion[i] * (1 - MOTION_ALPHA) + (if (d > 12) MOTION_ALPHA else 0f)
            if (motion[i] > MOTION_IGNORE) continue
            total += d
            n++
            if (d > 12) big++
        }
        return n > 0 && (total / n > 2f || big > maxOf(2f, n * 0.01f))
    }
}
