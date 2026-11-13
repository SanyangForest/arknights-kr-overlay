package com.akkr.overlay

import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.RectF
import kotlin.math.abs
import kotlin.math.min

/**
 * 상자 자리의 흑백 밝기 견본 (step 픽셀마다 한 점). 글자가 그대로 있는지, 몇 픽셀 움직였는지 볼 때 씀.
 * ink: 글자 획으로 보이는 점 (견본의 중간 밝기와 크게 다른 점). 배경이 움직여도(카드 그림, 안개 효과) 획은 그대로이므로
 * 획만 비교하면 글자가 바뀌었을 때만 다르게 나온다.
 */
class Patch(val x: Int, val y: Int, val cols: Int, val rows: Int, val step: Int, val v: FloatArray, val ink: BooleanArray)

/** 캡처 화면의 밝기 계산: 화면이 바뀌었는지, 상자 아래 글자가 바뀌었는지, 덮을 바탕색. */
object Frames {
    const val GW = 48 // 화면 변화를 재는 칸 수 (가로×세로)
    const val GH = 27

    fun lum(p: Int) = 0.299f * Color.red(p) + 0.587f * Color.green(p) + 0.114f * Color.blue(p)

    /** GW×GH 칸의 평균 밝기. */
    fun thumbnail(bmp: Bitmap): FloatArray {
        val small = Bitmap.createScaledBitmap(bmp, GW * 4, GH * 4, false)
        val px = IntArray(small.width * small.height)
        small.getPixels(px, 0, small.width, 0, 0, small.width, small.height)
        val sum = FloatArray(GW * GH)
        for (y in 0 until small.height) for (x in 0 until small.width) {
            sum[(y / 4) * GW + x / 4] += lum(px[y * small.width + x])
        }
        return FloatArray(GW * GH) { sum[it] / 16f }
    }

    /** 상자 자리의 밝기 (가로 12칸 × 세로 2~4칸, 칸마다 3×3점 평균). */
    fun signature(bmp: Bitmap, r: RectF): FloatArray {
        val l = r.left.coerceIn(0f, bmp.width - 1f)
        val t = r.top.coerceIn(0f, bmp.height - 1f)
        val w = (r.right.coerceIn(0f, bmp.width - 1f) - l).coerceAtLeast(1f)
        val h = (r.bottom.coerceIn(0f, bmp.height - 1f) - t).coerceAtLeast(1f)
        val cols = 12
        val rows = (h / (w / cols)).toInt().coerceIn(2, 4)
        return FloatArray(cols * rows) { i ->
            val cx = i % cols
            val cy = i / cols
            var s = 0f
            for (sy in 0 until 3) for (sx in 0 until 3) {
                val x = (l + (cx + (sx + 0.5f) / 3f) * w / cols).toInt().coerceIn(0, bmp.width - 1)
                val y = (t + (cy + (sy + 0.5f) / 3f) * h / rows).toInt().coerceIn(0, bmp.height - 1)
                s += lum(bmp.getPixel(x, y))
            }
            s / 9f
        }
    }

    /** 두 서명의 차이: (칸 평균, 가장 큰 칸). 크기가 다르면 아주 다른 것으로. */
    fun sigDiff(a: FloatArray, b: FloatArray): Pair<Float, Float> {
        if (a.size != b.size) return 255f to 255f
        var total = 0f
        var max = 0f
        for (i in a.indices) {
            val d = abs(a[i] - b[i])
            total += d
            if (d > max) max = d
        }
        return total / a.size to max
    }

    /** r 자리의 밝기 견본. */
    fun patch(bmp: Bitmap, r: RectF, step: Int = 3): Patch {
        val x = r.left.toInt().coerceIn(0, bmp.width - 1)
        val y = r.top.toInt().coerceIn(0, bmp.height - 1)
        val cols = ((r.right.toInt().coerceAtMost(bmp.width) - x) / step).coerceAtLeast(1)
        val rows = ((r.bottom.toInt().coerceAtMost(bmp.height) - y) / step).coerceAtLeast(1)
        val v = FloatArray(cols * rows) { i -> lum(bmp.getPixel((x + (i % cols) * step).coerceAtMost(bmp.width - 1), (y + (i / cols) * step).coerceAtMost(bmp.height - 1))) }
        // 획 = 바탕(중간 밝기)에서 글자 쪽(가장 밝거나 가장 어두운 쪽)으로 크게 떨어진 점.
        // 글자는 바탕보다 밝거나(어두운 막대 위 흰 글자) 어두우므로, 끝 10%와 바탕 중 먼 쪽을 글자 쪽으로 본다.
        val sorted = v.sorted()
        val median = sorted[v.size / 2]
        val hi = sorted[(v.size * 0.95f).toInt().coerceAtMost(v.size - 1)]
        val lo = sorted[(v.size * 0.05f).toInt()]
        val bright = hi - median >= median - lo
        return Patch(x, y, cols, rows, step, v, BooleanArray(v.size) { if (bright) v[it] - median > 40f else median - v[it] > 40f })
    }

    /**
     * 견본이 지금 화면에서 (dx, dy)만큼 옮겨 간 자리에 그대로 있나. range 픽셀 안을 2픽셀 간격으로 찾는다.
     * 찾으면 (dx, dy), 못 찾으면 null (글자가 바뀌었거나 사라짐).
     */
    fun findShift(bmp: Bitmap, p: Patch, range: Int, maxDiff: Float = 7f, offFrac: Float = 0.2f): Pair<Int, Int>? {
        val x0 = (p.x - range).coerceAtLeast(0)
        val y0 = (p.y - range).coerceAtLeast(0)
        val x1 = (p.x + p.cols * p.step + range).coerceAtMost(bmp.width)
        val y1 = (p.y + p.rows * p.step + range).coerceAtMost(bmp.height)
        val w = x1 - x0
        val h = y1 - y0
        if (w <= 0 || h <= 0) return null
        val px = IntArray(w * h)
        bmp.getPixels(px, 0, w, x0, y0, w, h)
        val lumArr = FloatArray(px.size) { lum(px[it]) }
        var best: Pair<Int, Int>? = null
        var bestDiff = maxDiff
        // 글자가 없는 밋밋한 자리는 어디로 옮겨도 맞으므로 견본에 밝기 차이가 있어야 함
        if ((p.v.maxOrNull() ?: 0f) - (p.v.minOrNull() ?: 0f) < 30f) return null
        // 획이 충분하면 획만 비교 (움직이는 배경은 보지 않음)
        val idx = p.v.indices.filter { p.ink[it] }.takeIf { it.size >= 8 } ?: p.v.indices.toList()
        var dy = -range
        while (dy <= range) {
            var dx = -range
            while (dx <= range) {
                var sum = 0f
                var n = 0
                var off = 0
                for (i in idx) {
                    val sx = p.x + (i % p.cols) * p.step + dx - x0
                    val sy = p.y + (i / p.cols) * p.step + dy - y0
                    if (sx < 0 || sy < 0 || sx >= w || sy >= h) continue
                    val diff = abs(p.v[i] - lumArr[sy * w + sx])
                    sum += diff
                    if (diff > 35f) off++
                    n++
                }
                if (n > idx.size * 0.8f) {
                    // 획으로 볼 때: 획 대부분(80%)이 그대로면 맞음 (반투명 막대 뒤로 지나가는 것이 몇 점 바꿔도 됨)
                    val d = if (idx.size < p.v.size && off <= n * offFrac) min(sum / n, maxDiff - 0.01f) else sum / n
                    if (d < bestDiff || (d == bestDiff && best != null && abs(dx) + abs(dy) < abs(best.first) + abs(best.second))) {
                        bestDiff = d
                        best = dx to dy
                    }
                }
                dx += 2
            }
            dy += 2
        }
        return best
    }

    /**
     * 원문 상자 바로 바깥 테두리의 중앙값 색 = 덮을 바탕색.
     * 글자색은 원문 글자색(상자 안에서 바탕과 가장 다른 픽셀들의 중앙값)이 바탕과 충분히 다르면 그 색, 아니면 밝기에 따라 흰색/검은색.
     */
    fun colors(bmp: Bitmap, box: RectF): Pair<Int, Int> {
        val l = (box.left - 2).toInt().coerceIn(0, bmp.width - 1)
        val r = (box.right + 2).toInt().coerceIn(0, bmp.width - 1)
        val t = (box.top - 2).toInt().coerceIn(0, bmp.height - 1)
        val btm = (box.bottom + 2).toInt().coerceIn(0, bmp.height - 1)
        val edge = buildList {
            for (x in l..r step 2) { add(bmp.getPixel(x, t)); add(bmp.getPixel(x, btm)) }
            for (y in t..btm step 2) { add(bmp.getPixel(l, y)); add(bmp.getPixel(r, y)) }
        }
        fun med(px: List<Int>, ch: (Int) -> Int) = px.map(ch).sorted()[px.size / 2]
        val c = Color.rgb(med(edge, Color::red), med(edge, Color::green), med(edge, Color::blue))
        fun lum(p: Int) = 0.299 * Color.red(p) + 0.587 * Color.green(p) + 0.114 * Color.blue(p)
        fun dist(p: Int) = abs(Color.red(p) - Color.red(c)) + abs(Color.green(p) - Color.green(c)) + abs(Color.blue(p) - Color.blue(c))
        // 큰 문단도 몇천 점만 본다 (getPixel이 느림)
        val sx = maxOf(2, (r - l) / 80)
        val sy = maxOf(2, (btm - t) / 40)
        val inside = buildList {
            for (y in (t + 2)..(btm - 2) step sy) for (x in (l + 2)..(r - 2) step sx) add(bmp.getPixel(x, y))
        }
        val far = inside.filter { dist(it) > 120 }.sortedBy { dist(it) }
        if (inside.size >= 20 && far.size >= inside.size * 0.03) {
            val top = far.takeLast(maxOf(1, far.size / 4))
            val fg = Color.rgb(med(top, Color::red), med(top, Color::green), med(top, Color::blue))
            if (abs(lum(fg) - lum(c)) >= 90) return c to fg
        }
        return c to (if (lum(c) < 140) Color.WHITE else Color.rgb(17, 17, 17))
    }
}
