package com.akkr.overlay

import android.content.Context
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.style.ForegroundColorSpan
import android.text.style.RelativeSizeSpan
import android.text.style.StyleSpan
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import org.json.JSONArray
import org.json.JSONObject

/**
 * 위수협의(卫戍协议) 추천 조합 패널: 화면 왼쪽 가운데의 작은 단추로 연다. 열면 닫을 때까지 그대로 있다.
 *
 *  - 단추는 위수협의 화면일 때만 보인다: 로비 고정 문구('卫戍协议' 등)나 인연 이름이 한 화면에 셋 이상 읽히면
 *    그때부터 3분. 게임 창이 화면에서 사라지면 단추와 패널을 모두 숨긴다.
 *  - 내용은 사전의 meta 'autochess' (build_autochess.py가 게임 데이터의 인연·비용으로 만든 추천 조합).
 *  - 단추와 패널은 오버레이 자신의 창이라 누를 수 있다. 게임에는 아무 입력도 보내지 않는다.
 */
class AutochessPanel(private val ctx: Context, private val wm: WindowManager, json: String) {

    companion object {
        private const val SHOW_MS = 180_000L
        // 비용 색 (1 회색 · 2 초록 · 3 파랑 · 4 보라 · 5 노랑 · 6 주황)
        private val COST_COLOR = intArrayOf(0, 0xFFB4B4B4.toInt(), 0xFFA6E07A.toInt(), 0xFF6EB4FF.toInt(),
            0xFFCDAAFF.toInt(), 0xFFFFD040.toInt(), 0xFFFF8A3D.toInt())
        private const val BOND_COLOR = 0xFFFFE08A.toInt()
        private const val DIM = 0xFFAAAAAA.toInt()
    }

    private val data = JSONObject(json)
    private val anchors = data.optJSONArray("anchors").strings()
    private val bondNames = data.optJSONArray("bond_names").strings().toSet()
    private val main = Handler(Looper.getMainLooper())
    private var button: TextView? = null
    private var panel: View? = null
    @Volatile private var lastSeen = 0L

    /** 화면 읽기 스레드에서: 이번에 읽은 원문들로 위수협의 화면인지 보고 단추를 보이거나 숨긴다. */
    fun observe(texts: Collection<String>) {
        val now = SystemClock.uptimeMillis()
        if (texts.any { t -> anchors.any { it in t } } || texts.count { it in bondNames } >= 3) lastSeen = now
        val want = lastSeen != 0L && now - lastSeen < SHOW_MS
        main.post { showButton(want || panel != null) }
    }

    /** 게임 창이 안 보일 때: 단추와 패널을 모두 숨긴다. */
    fun hide() = main.post {
        closePanel()
        showButton(false)
    }

    fun destroy() = main.post {
        closePanel()
        button?.let { b -> runCatching { wm.removeView(b) } }
        button = null
    }

    private fun dp(v: Float) = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, ctx.resources.displayMetrics)

    private fun params(w: Int, h: Int, x: Int) = WindowManager.LayoutParams(
        w, h, WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE, PixelFormat.TRANSLUCENT,
    ).apply {
        gravity = Gravity.START or Gravity.CENTER_VERTICAL
        this.x = x
    }

    private fun showButton(on: Boolean) {
        val b = button
        if (on && b == null) {
            val v = TextView(ctx).apply {
                text = "위수\n조합"
                setTextColor(Color.WHITE)
                setTextSize(TypedValue.COMPLEX_UNIT_DIP, 12f)
                gravity = Gravity.CENTER
                typeface = Typeface.DEFAULT_BOLD
                background = GradientDrawable().apply {
                    setColor(0xCC1C2A44.toInt())
                    cornerRadii = floatArrayOf(0f, 0f, dp(10f), dp(10f), dp(10f), dp(10f), 0f, 0f)
                }
                setPadding(dp(4f).toInt(), dp(8f).toInt(), dp(4f).toInt(), dp(8f).toInt())
                setOnClickListener { if (panel == null) openPanel() else closePanel() }
            }
            wm.addView(v, params(dp(40f).toInt(), WindowManager.LayoutParams.WRAP_CONTENT, 0))
            button = v
        } else if (!on && b != null) {
            runCatching { wm.removeView(b) }
            button = null
        }
    }

    private fun openPanel() {
        val metrics = ctx.resources.displayMetrics
        val width = minOf((metrics.widthPixels * 0.5f).toInt(), dp(420f).toInt())
        val height = (metrics.heightPixels * 0.92f).toInt()
        val body = TextView(ctx).apply {
            text = content()
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_DIP, 12.5f)
            setLineSpacing(0f, 1.15f)
            setPadding(dp(12f).toInt(), dp(4f).toInt(), dp(12f).toInt(), dp(12f).toInt())
        }
        val close = TextView(ctx).apply {
            text = "위수협의 추천 조합   ✕ 닫기"
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_DIP, 14f)
            typeface = Typeface.DEFAULT_BOLD
            setPadding(dp(12f).toInt(), dp(10f).toInt(), dp(12f).toInt(), dp(6f).toInt())
            setOnClickListener { closePanel() }
        }
        val root = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            background = GradientDrawable().apply {
                setColor(0xEE12141A.toInt())
                cornerRadius = dp(10f)
                setStroke(1, 0x33FFFFFF)
            }
            addView(close)
            addView(ScrollView(ctx).apply { addView(body) }, LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.MATCH_PARENT))
        }
        // 단추 바로 오른쪽에 붙여 단추로 다시 닫을 수 있게
        wm.addView(root, params(width, height, dp(44f).toInt()))
        panel = root
    }

    private fun closePanel() {
        panel?.let { p -> runCatching { wm.removeView(p) } }
        panel = null
    }

    /** 패널 글: 국가 인연별 추천 조합(초반 3명 · 완성 6명 + 함께 켜지는 인연), 그 아래 성향 인연별 오퍼레이터. */
    private fun content(): CharSequence {
        val sb = SpannableStringBuilder()
        fun span(text: String, vararg what: Any) {
            val start = sb.length
            sb.append(text)
            for (w in what) sb.setSpan(w, start, sb.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
        }
        fun members(arr: JSONArray) {
            for (i in 0 until arr.length()) {
                val m = arr.getJSONObject(i)
                val cost = m.optInt("cost").coerceIn(0, 6)
                if (i > 0) sb.append("  ")
                span(m.optString("ko"), ForegroundColorSpan(COST_COLOR[cost]))
                span("$cost", ForegroundColorSpan(DIM), RelativeSizeSpan(0.8f))
            }
        }
        span("오퍼레이터 이름 옆 숫자 = 비용. 국가 인연은 3명·6명, 성향 인연은 2명부터 켜져요.\n\n",
            ForegroundColorSpan(DIM), RelativeSizeSpan(0.9f))
        val comps = data.optJSONArray("comps") ?: JSONArray()
        for (i in 0 until comps.length()) {
            val c = comps.getJSONObject(i)
            span("■ ${c.optString("bond")}\n", ForegroundColorSpan(BOND_COLOR), StyleSpan(Typeface.BOLD))
            span("초반 3  ", ForegroundColorSpan(DIM))
            members(c.getJSONArray("early"))
            sb.append('\n')
            span("완성 6  ", ForegroundColorSpan(DIM))
            members(c.getJSONArray("full"))
            val extra = c.optJSONArray("extra")
            if (extra != null && extra.length() > 0) {
                sb.append('\n')
                span("함께 켜짐  ", ForegroundColorSpan(DIM))
                for (j in 0 until extra.length()) {
                    val e = extra.getJSONObject(j)
                    if (j > 0) sb.append(", ")
                    span("${e.optString("ko")} ${e.optInt("count")}", ForegroundColorSpan(BOND_COLOR))
                }
            }
            sb.append("\n\n")
        }
        span("성향 인연\n", StyleSpan(Typeface.BOLD))
        val bonds = data.optJSONArray("bonds") ?: JSONArray()
        for (i in 0 until bonds.length()) {
            val b = bonds.getJSONObject(i)
            if (b.optString("kind") != "style") continue
            val need = b.optJSONArray("need").strings().joinToString("·")
            span("■ ${b.optString("ko")}", ForegroundColorSpan(BOND_COLOR), StyleSpan(Typeface.BOLD))
            span(if (need.isNotEmpty()) " ($need)\n" else "\n", ForegroundColorSpan(DIM))
            members(b.getJSONArray("members"))
            sb.append("\n\n")
        }
        return sb
    }

    private fun JSONArray?.strings(): List<String> =
        if (this == null) emptyList() else (0 until length()).map { optString(it) }
}
