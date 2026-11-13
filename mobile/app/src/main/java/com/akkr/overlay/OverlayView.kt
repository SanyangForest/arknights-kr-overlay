package com.akkr.overlay

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Typeface
import android.text.StaticLayout
import android.text.TextPaint
import android.text.TextUtils
import android.view.View
import kotlin.math.max
import kotlin.math.min

/**
 * 화면에 그릴 번역 상자. tail이면 윗줄에서 문장째 번역한 줄바꿈 꼬리라 원문만 가린다.
 * rows가 있으면 공개모집 확정 조합 패널 (box는 태그 무리 자리, 줄마다 (글, 등급)).
 */
data class Item(
    val box: RectF, val ko: String, val zh: String, val lineH: Float,
    val bg: Int, val fg: Int, val tail: Boolean = false, val rows: List<Pair<String, Int>>? = null,
)

/** 그릴 준비가 된 번역 상자: 글자 배치와 실제로 덮는 자리(번역문이 길면 원문 칸보다 넓다). */
class Placed(val item: Item, val layout: StaticLayout?, val textTop: Float, val drawn: RectF) {
    /** 원문이 (dx, dy)만큼 움직였을 때 따라 옮긴 상자 (홈 메뉴판의 기울기 효과). */
    fun moved(dx: Float, dy: Float) = Placed(
        item.copy(box = RectF(item.box).apply { offset(dx, dy) }), layout, textTop + dy,
        RectF(drawn).apply { offset(dx, dy) },
    )
}

/** 번역 상자 배치. 화면 읽기 쪽(백그라운드)에서 미리 계산해 덮는 자리를 알 수 있게 한다. */
object Painter {
    private const val PAD = 3f

    /**
     * others: 같은 화면의 다른 원문 자리. 번역문이 길어도 오른쪽 이웃(옆 카드의 이름 등)을 덮을 만큼 넓히지 않는다.
     * screenH: 공개모집 패널을 태그 아래에 둘 자리가 있는지 볼 때만 씀 (0이면 늘 아래).
     */
    fun place(item: Item, screenW: Int, others: List<RectF> = emptyList(), screenH: Int = 0): Placed {
        val b = item.box
        if (item.rows != null) return placePanel(item, screenW, screenH)
        if (item.tail) return Placed(item, null, 0f, RectF(b.left - 2, b.top - 2, b.right + 2, b.bottom + 2))
        // 상자마다 따로 만든 TextPaint (그리는 쪽과 함께 쓰지 않음)
        val paint = TextPaint(Paint.ANTI_ALIAS_FLAG).apply { color = item.fg }
        val rightLimit = others.filter { o -> o.left >= b.right - 2 && o.top < b.bottom && o.bottom > b.top }.minOfOrNull { it.left }
        val layout = fit(item, paint, rightLimit ?: screenW.toFloat())
        val textW = (0 until layout.lineCount).maxOf { layout.getLineWidth(it) }
        // 번역문이 원문 칸보다 낮으면 세로 가운데
        val top = b.top + max(0f, (b.height() - layout.height) / 2f)
        val drawn = RectF(
            b.left - PAD, min(b.top, top) - PAD,
            max(b.right, b.left + textW) + PAD, max(b.bottom, top + layout.height) + PAD,
        )
        return Placed(item, layout, top, drawn)
    }

    /**
     * 원문 글자 높이에서 시작해, 원문 칸에 들어갈 때까지 줄이고 필요하면 오른쪽으로 조금 넓힌다 (rightLimit 앞까지).
     * 세로 글자 칸('干/员')은 칸 너비를 글자 크기로 보고, 더 작게까지 줄일 수 있다.
     */
    private fun fit(it: Item, paint: TextPaint, rightLimit: Float): StaticLayout {
        val boxW = max(it.box.width(), 12f)
        val vertical = it.box.height() > it.box.width() * 1.5f
        // 세로 글자 칸('干/员')은 한국어가 한 줄에 들어가게 오른쪽 빈자리까지 더 넓힐 수 있다 (이웃 글자 앞까지)
        val wider = max(boxW, min(boxW * (if (vertical) 3.5f else 1.6f), rightLimit - it.box.left - 4f))
        val base = min((if (vertical) it.box.width() else it.lineH) * 0.85f, 80f)
        val smallest = base * (if (vertical) 0.3f else 0.45f)
        var size = base
        while (true) {
            paint.textSize = size
            for (w in listOf(boxW, wider)) {
                val l = layout(it.ko, paint, w)
                if (l.height <= it.box.height() * 1.25f || size <= smallest) return l
            }
            size *= 0.9f
        }
    }

    private fun layout(text: String, paint: TextPaint, width: Float) =
        StaticLayout.Builder.obtain(text, 0, text.length, paint, width.toInt().coerceAtLeast(1))
            .setIncludePad(false)
            .build()

    /** 공개모집 패널 글자 크기 (태그 글자 높이에 맞춤). */
    fun panelTextSize(item: Item) = (item.lineH * 0.7f).coerceIn(18f, 56f)

    fun panelPaint(size: Float, bold: Boolean = false) = TextPaint(Paint.ANTI_ALIAS_FLAG).apply {
        textSize = size
        if (bold) typeface = Typeface.DEFAULT_BOLD
    }

    /** 공개모집 패널 자리: 태그 무리 바로 아래, 자리가 없으면 위. */
    private fun placePanel(item: Item, screenW: Int, screenH: Int): Placed {
        val rows = item.rows.orEmpty()
        val size = panelTextSize(item)
        val title = panelPaint(size, bold = true)
        val body = panelPaint(size)
        val line = body.fontSpacing
        val width = min(
            (listOf(title.measureText(Recruit.TITLE)) + rows.map { body.measureText(it.first) }).max() + size * 2,
            screenW - 20f,
        )
        val height = line * (rows.size + 1) + size * 1.5f
        val b = item.box
        val gap = size / 2
        val x = min(max(10f, b.left), screenW - width - 10f).coerceAtLeast(10f)
        val top = if (screenH == 0 || b.bottom + gap + height <= screenH - 10) b.bottom + gap else max(10f, b.top - gap - height)
        return Placed(item, null, 0f, RectF(x, top, x + width, top + height))
    }
}

/** 게임 위에 번역 상자를 그리는 투명한 창. 터치는 아래 게임으로 넘어간다. */
class OverlayView(context: Context) : View(context) {
    @Volatile private var placed: List<Placed> = emptyList()
    @Volatile private var offset = 0f to 0f
    private val bgPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val edgePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 1.5f
        color = Color.argb(40, 255, 255, 255)
    }
    private val loc = IntArray(2)

    /** offX, offY: 상자 좌표의 기준(캡처한 게임 창)이 화면에서 놓인 자리. 화면 전체를 캡처하면 0. */
    fun show(list: List<Placed>, offX: Float = 0f, offY: Float = 0f) {
        offset = offX to offY
        placed = list
        postInvalidate()
    }

    override fun onDraw(canvas: Canvas) {
        // 창이 상태 표시줄 아래에서 시작하는 기기도 있으므로 화면 좌표에서 창 위치를 뺀다
        getLocationOnScreen(loc)
        canvas.save()
        canvas.translate(offset.first - loc[0], offset.second - loc[1])
        // 큰 상자부터 그려서 옆의 작은 글자가 큰 상자 바탕에 덮이지 않게. 공개모집 패널은 맨 위에
        val order = placed.sortedWith(compareBy({ it.item.rows != null }, { -it.item.box.width() * it.item.box.height() }))
        for (p in order) {
            if (p.item.rows != null) {
                drawPanel(canvas, p)
                continue
            }
            bgPaint.color = p.item.bg
            val r = min(8f, p.drawn.height() / 3f)
            canvas.drawRoundRect(p.drawn, r, r, bgPaint)
            val layout = p.layout ?: continue
            canvas.save()
            canvas.translate(p.item.box.left, p.textTop)
            layout.draw(canvas)
            canvas.restore()
        }
        canvas.restore()
    }

    private fun drawPanel(canvas: Canvas, p: Placed) {
        val size = Painter.panelTextSize(p.item)
        val r = size * 0.6f
        bgPaint.color = Color.argb(238, 18, 20, 26)
        canvas.drawRoundRect(p.drawn, r, r, bgPaint)
        canvas.drawRoundRect(p.drawn, r, r, edgePaint)
        val title = Painter.panelPaint(size, bold = true).apply { color = Color.WHITE }
        val body = Painter.panelPaint(size)
        val x = p.drawn.left + size
        val avail = p.drawn.width() - size * 2
        var y = p.drawn.top + size * 0.5f - title.ascent()
        canvas.drawText(Recruit.TITLE, x, y, title)
        y += size * 0.5f
        for ((text, rarity) in p.item.rows.orEmpty()) {
            y += body.fontSpacing
            body.color = RARITY_COLOR[rarity] ?: Color.rgb(220, 220, 220)
            canvas.drawText(TextUtils.ellipsize(text, body, avail, TextUtils.TruncateAt.END).toString(), x, y, body)
        }
    }

    companion object {
        /** 등급 색 (공개모집 패널): 6★ 주황, 5★ 노랑, 4★ 보라 */
        private val RARITY_COLOR = mapOf(
            6 to Color.rgb(255, 127, 39), 5 to Color.rgb(255, 208, 64), 4 to Color.rgb(205, 170, 255),
            3 to Color.rgb(170, 170, 170), 1 to Color.rgb(190, 220, 255),
        )
    }
}
