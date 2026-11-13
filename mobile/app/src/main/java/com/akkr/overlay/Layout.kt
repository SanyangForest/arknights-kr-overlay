package com.akkr.overlay

import android.graphics.RectF
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

/** OCR 한 줄 (화면 픽셀 좌표). */
data class OcrLine(val text: String, val box: RectF)

/** 화면에 표시할 단위: 줄바꿈된 같은 문장은 여러 줄을 합친 것. */
class Block(first: OcrLine) {
    var text = first.text
    val box = RectF(first.box)
    val parts = mutableListOf(first)
    private val heights = mutableListOf(first.box.height())
    var lastBox = RectF(first.box)
    val lineH: Float get() = heights.sorted()[heights.size / 2]
    val lines: Int get() = parts.size

    fun add(line: OcrLine) {
        text += line.text
        box.union(line.box)
        parts += line
        heights += line.box.height()
        lastBox = RectF(line.box)
    }
}

/**
 * 줄바꿈된 같은 문장을 하나로 합친다 (PC판 overlay/layout.py의 merge_lines를 옮긴 것).
 *
 * 바로 아래 줄이고 왼쪽 정렬(또는 가운데 정렬)이며 글자 높이가 비슷할 때,
 *  - 윗줄이 넓거나(대사창), 이은 문장이 사전 문장의 앞부분이면 합친다.
 *  - 각각 사전에 그대로 있는 두 줄은 이은 문장이 사전 문장의 '정확한' 앞부분일 때만 합친다.
 * 합친 문장을 사전에서 못 찾으면 원래 줄들로 되돌린다.
 */
fun mergeLines(lines: List<OcrLine>, screenW: Int, m: Matcher): List<Block> {
    val merged = mutableListOf<Block>()
    for (b in lines.sortedWith(compareBy({ it.box.top }, { it.box.left }))) {
        val lh = b.box.height()
        val cy = b.box.centerY()
        val target = merged.firstOrNull { u ->
            if (!Text.hasHan(b.text)) return@firstOrNull false
            val last = u.lastBox
            val lastLh = last.height()
            val ratio = lh / max(lastLh, 1f)
            val dy = cy - last.centerY()
            val unit = min(lh, lastLh)
            if (dy < 0.6f * unit || dy > 2.2f * max(lh, lastLh)) return@firstOrNull false
            val leftAligned = abs(u.box.left - b.box.left) < unit * 0.8f
            val centered = abs(last.centerX() - b.box.centerX()) < unit * 2
            if (!leftAligned && !centered && b.text.length > 3) return@firstOrNull false
            val joined = u.text + b.text
            val exactKnown = m.canContinue(joined, fuzzy = false)
            var joinedKnown = exactKnown || m.canContinue(joined)
            if (joinedKnown && !exactKnown && m.isEntry(u.text) && m.isEntry(b.text)) joinedKnown = false
            val shortTail = b.text.length <= 3 && b.box.centerX() in u.box.left..u.box.right && ratio in 0.6f..1.6f && exactKnown
            var wide = leftAligned && last.width() >= screenW * 0.3f && ratio in 0.7f..1.45f
            if (wide && !joinedKnown && m.isEntry(b.text)) wide = false
            wide || shortTail || (centered && ratio in 0.7f..1.45f && joinedKnown) ||
                (leftAligned && ratio in 0.6f..1.7f && joinedKnown)
        }
        if (target != null) target.add(b) else merged += Block(b)
    }
    // 합친 문장을 사전에서 못 찾으면 원래 줄들로 (스탯 표의 '生命上限 1888' 아래 '攻击'처럼 잘못 합친 것)
    return merged.flatMap { u ->
        if (u.lines > 1 && m.lookup(u.text) == null) u.parts.map { Block(it) } else listOf(u)
    }
}
