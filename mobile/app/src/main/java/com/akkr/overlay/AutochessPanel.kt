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
 *  - 단추는 위수협의 화면일 때만 보인다: 로비 고정 문구('卫戍协议' 등)나 맹약 이름이 한 화면에 셋 이상 읽히면
 *    그때부터 3분. 게임 창이 화면에서 사라지면 단추와 패널을 모두 숨긴다.
 *  - 내용은 사전의 meta 'autochess' (build_autochess.py): 탭 셋
 *      커뮤니티 추천 = bilibili 공략 영상 조합 (ak-sweep 정리, 제목·요약은 번역)
 *      국가별 = 게임 데이터의 맹약·비용으로 계산한 조합, 맹약 = 성향 맹약 효과와 오퍼레이터
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
        private val TABS = listOf("커뮤니티 추천", "국가별", "맹약")
    }

    private val data = JSONObject(json)
    private val anchors = data.optJSONArray("anchors").strings()
    private val bondNames = data.optJSONArray("bond_names").strings().toSet()
    private val main = Handler(Looper.getMainLooper())
    private var button: TextView? = null
    private var panel: View? = null
    @Volatile private var lastSeen = 0L
    private var tab = 0

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
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_DIP, 12.5f)
            setLineSpacing(0f, 1.15f)
            setPadding(dp(12f).toInt(), dp(4f).toInt(), dp(12f).toInt(), dp(12f).toInt())
        }
        val scroll = ScrollView(ctx).apply { addView(body) }
        val close = TextView(ctx).apply {
            text = "위수협의 추천 조합   ✕ 닫기"
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_DIP, 14f)
            typeface = Typeface.DEFAULT_BOLD
            setPadding(dp(12f).toInt(), dp(10f).toInt(), dp(12f).toInt(), dp(4f).toInt())
            setOnClickListener { closePanel() }
        }
        // 탭: 커뮤니티 추천 / 국가별 / 맹약
        val tabRow = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(dp(6f).toInt(), 0, dp(6f).toInt(), dp(2f).toInt())
        }
        val tabViews = TABS.map { name ->
            TextView(ctx).apply {
                text = name
                setTextSize(TypedValue.COMPLEX_UNIT_DIP, 13f)
                setPadding(dp(6f).toInt(), dp(6f).toInt(), dp(8f).toInt(), dp(6f).toInt())
                tabRow.addView(this)
            }
        }
        fun select(i: Int) {
            tab = i
            tabViews.forEachIndexed { j, v ->
                v.setTextColor(if (j == i) BOND_COLOR else DIM)
                v.typeface = if (j == i) Typeface.DEFAULT_BOLD else Typeface.DEFAULT
            }
            body.text = when (i) { 0 -> community(); 1 -> nations(); else -> styles() }
            scroll.scrollTo(0, 0)
        }
        tabViews.forEachIndexed { i, v -> v.setOnClickListener { select(i) } }
        select(if (tab == 0 && (data.optJSONArray("community")?.length() ?: 0) == 0) 1 else tab)
        val root = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            background = GradientDrawable().apply {
                setColor(0xEE12141A.toInt())
                cornerRadius = dp(10f)
                setStroke(1, 0x33FFFFFF)
            }
            addView(close)
            addView(tabRow)
            addView(scroll, LinearLayout.LayoutParams(
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

    /** 글 조각을 덧붙이는 도우미 (색·크기·굵기 span) */
    private class Text {
        val sb = SpannableStringBuilder()
        fun span(text: String, vararg what: Any) {
            val start = sb.length
            sb.append(text)
            for (w in what) sb.setSpan(w, start, sb.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
        }
        fun dim(text: String, size: Float = 1f) = span(text, ForegroundColorSpan(DIM), RelativeSizeSpan(size))
        fun title(text: String) = span(text, ForegroundColorSpan(BOND_COLOR), StyleSpan(Typeface.BOLD))
        /** 오퍼레이터 목록: 이름은 비용 색, 옆에 작은 비용 숫자 (비용 0 = 상점에 없는 자체 편성 오퍼레이터 ★) */
        fun members(arr: JSONArray?) {
            if (arr == null) return
            for (i in 0 until arr.length()) {
                val m = arr.getJSONObject(i)
                val cost = m.optInt("cost").coerceIn(0, 6)
                if (i > 0) sb.append("  ")
                span(m.optString("ko"), ForegroundColorSpan(if (cost == 0) Color.WHITE else COST_COLOR[cost]))
                span(if (cost == 0) "★" else "$cost", ForegroundColorSpan(DIM), RelativeSizeSpan(0.8f))
            }
        }
    }

    /** 커뮤니티 추천: bilibili 공략 영상의 조합 (build_autochess.py가 extra/autochess_community.json에서 가져옴) */
    private fun community(): CharSequence {
        val t = Text()
        val stats = data.optString("community_stats")
        t.dim("bilibili 공략 영상의 조합 (유형별 즐겨찾기 1위${if (stats.isNotEmpty()) ", $stats 기준" else ""}). " +
            "이름 옆 숫자 = 비용, ★ = 자체 편성. N레벨 = 상점 레벨.\n\n", 0.9f)
        val list = data.optJSONArray("community") ?: JSONArray()
        for (i in 0 until list.length()) {
            val c = list.getJSONObject(i)
            t.title("■ ${c.optString("title")}\n")
            val bonds = c.optJSONArray("bonds").strings()
            if (bonds.isNotEmpty()) {
                t.dim("맹약  ")
                t.span(bonds.joinToString(" · ") + "\n", ForegroundColorSpan(BOND_COLOR))
            }
            val stages = c.optJSONArray("stages") ?: JSONArray()
            for (j in 0 until stages.length()) {
                val st = stages.getJSONObject(j)
                t.dim("${st.optString("label")}  ")
                t.members(st.optJSONArray("ops"))
                t.sb.append('\n')
            }
            val carry = c.optJSONArray("carry").strings()
            if (carry.isNotEmpty()) {
                t.dim("핵심  ")
                t.span(carry.joinToString(" · ") + "\n", StyleSpan(Typeface.BOLD))
            }
            t.span(c.optString("summary") + "\n", ForegroundColorSpan(0xFFDDDDDD.toInt()), RelativeSizeSpan(0.95f))
            t.dim("작성 ${c.optString("author")} · 즐겨찾기 ${"%,d".format(c.optInt("fav"))} · ${c.optString("published")}\n\n", 0.85f)
        }
        t.dim("출처: 각 영상 작성자 (bilibili), 정리 ak-sweep (github.com/dazauzai/ak-sweep)", 0.85f)
        return t.sb
    }

    /** 국가 맹약별 추천 조합 (초반 3명 · 완성 6명 + 함께 켜지는 맹약): 게임 데이터의 비용으로 계산한 것 */
    private fun nations(): CharSequence {
        val t = Text()
        t.dim("게임 데이터로 계산한 조합: 초반 = 가장 싼 3명, 완성 = 성향 맹약이 가장 많이 켜지는 6명. " +
            "이름 옆 숫자 = 비용. 국가 맹약은 3명·6명부터 켜져요.\n\n", 0.9f)
        val comps = data.optJSONArray("comps") ?: JSONArray()
        for (i in 0 until comps.length()) {
            val c = comps.getJSONObject(i)
            t.title("■ ${c.optString("bond")}\n")
            t.dim("초반 3  ")
            t.members(c.optJSONArray("early"))
            t.sb.append('\n')
            t.dim("완성 6  ")
            t.members(c.optJSONArray("full"))
            val extra = c.optJSONArray("extra")
            if (extra != null && extra.length() > 0) {
                t.sb.append('\n')
                t.dim("함께 켜짐  ")
                for (j in 0 until extra.length()) {
                    val e = extra.getJSONObject(j)
                    if (j > 0) t.sb.append(", ")
                    t.span("${e.optString("ko")} ${e.optInt("count")}", ForegroundColorSpan(BOND_COLOR))
                }
            }
            t.sb.append("\n\n")
        }
        return t.sb
    }

    /** 성향 맹약: 발동 인원, 효과, 소속 오퍼레이터 */
    private fun styles(): CharSequence {
        val t = Text()
        val bonds = data.optJSONArray("bonds") ?: JSONArray()
        for (i in 0 until bonds.length()) {
            val b = bonds.getJSONObject(i)
            if (b.optString("kind") != "style") continue
            val need = b.optJSONArray("need").strings().joinToString("·")
            t.title("■ ${b.optString("ko")}")
            t.dim(if (need.isNotEmpty()) " (${need}명)\n" else "\n")
            val desc = b.optString("desc")
            if (desc.isNotEmpty()) t.span(desc + "\n", ForegroundColorSpan(0xFFCCCCCC.toInt()), RelativeSizeSpan(0.9f))
            t.members(b.optJSONArray("members"))
            t.sb.append("\n\n")
        }
        return t.sb
    }

    private fun JSONArray?.strings(): List<String> =
        if (this == null) emptyList() else (0 until length()).map { optString(it) }
}
