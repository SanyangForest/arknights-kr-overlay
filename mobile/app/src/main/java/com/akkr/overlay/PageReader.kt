package com.akkr.overlay

import android.graphics.Bitmap
import android.graphics.RectF
import android.util.Log
import com.google.android.gms.tasks.Tasks
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.chinese.ChineseTextRecognizerOptions
import kotlin.math.max
import kotlin.math.min

/** 화면 한 장을 읽어(ML Kit) 줄을 합치고 사전에서 찾아 번역 상자 목록을 만든다. */
class PageReader(private val matcher: Matcher, private val recruit: Recruit? = null) {
    private val recognizer = TextRecognition.getClient(ChineseTextRecognizerOptions.Builder().build())

    /**
     * covered: 번역 상자가 덮고 있는 자리 (상자가 캡처에 찍히는 화면 녹화 방식에서만). 거기서 읽힌 글자는 버린다.
     * olds: 이미 띄운 상자. 줄바꿈된 꼬리 줄이 윗줄의 문장을 이어받는지 볼 때 씀.
     */
    fun read(bmp: Bitmap, covered: List<RectF> = emptyList(), olds: List<Item> = emptyList()): List<Item> {
        val lines = readLines(bmp, covered)
        val blocks = mergeLines(lines, bmp.width, matcher)
        val items = mutableListOf<Item>()
        for (blk in blocks) {
            val hit = matcher.lookup(blk.text)
            val (bgc, fg) = Frames.colors(bmp, blk.box)
            // 윗줄에서 문장 전체를 이미 번역했으면 줄바꿈된 꼬리는 원문만 가린다 (꼬리도 원래 문장을 이어받음)
            val above = (olds + items).lastOrNull { u ->
                val gap = blk.box.top - u.box.bottom
                gap >= -blk.lineH * 0.5f && gap < blk.lineH * 1.5f &&
                    u.box.left <= blk.box.right && blk.box.left <= u.box.right && matcher.isTailOf(blk.text, u.zh)
            }
            // 짧은 마지막 줄이 사전에 따로 있어도('快点.') 윗줄 문장의 끝이면 꼬리로 본다 (대사가 두 번 번역되지 않게)
            // 따로 찾은 번역이 윗줄 문장 안에 이미 들어 있는 조각이어도('（期间可随时停止技能）') 꼬리로 본다 (두 번 보이지 않게)
            val endOfAbove = above != null && (Text.hanCount(blk.text) <= 4 && matcher.isEndOf(blk.text, above.zh) ||
                hit != null && above.ko.isNotEmpty() && Text.normalize(above.zh).contains(Text.normalize(hit.zh)))
            if (hit != null) Log.v(TAG, "번역(${hit.how}): ${blk.text} → ${hit.ko.take(30)}")
            if (hit != null && !endOfAbove) {
                items += Item(stretched(blk.box, blk.text, hit), hit.ko, hit.zh, blk.lineH, bgc, fg)
                continue
            }
            val owner = items.indexOf(above)
            if (above != null && owner >= 0 && !above.tail) {
                // 문단째 번역한 윗줄 상자를 꼬리 줄까지 넓힌다: 번역문이 첫 줄 칸에 작게 몰리지 않고 문단 자리에 원래 크기로 들어감
                items[owner] = above.copy(box = RectF(above.box).apply { union(blk.box) })
            } else if (above != null) items += Item(RectF(blk.box), "", above.zh, blk.lineH, bgc, fg, tail = true)
            else Log.d(TAG, "번역 없음: ${blk.text}")
        }
        recruitPanel(lines)?.let { items += it }
        return items
    }

    /** 공개모집 태그 고르기 창('招募时限'이 보이고 태그가 3개 이상)이면 4★ 이상 확정 조합 패널. */
    private fun recruitPanel(lines: List<OcrLine>): Item? {
        val r = recruit ?: return null
        val texts = lines.map { Text.normalize(Text.simplify(it.text)) }
        val ids = r.tagIds(texts)
        // 태그 고르기 창에만 있는 '招募时限'이 보여야 한다 (모집 목록 화면의 칸별 태그를 모아 엉뚱한 조합을 내지 않게)
        if (ids.size < 3 || texts.none { "招募时限" in it }) return null
        val boxes = lines.filterIndexed { i, _ -> r.isTag(texts[i]) }.map { it.box }
        val box = RectF(boxes[0]).apply { boxes.forEach { union(it) } }
        val lineH = boxes.map { it.height() }.sorted()[boxes.size / 2]
        val rows = r.describe(ids)
        return Item(box, rows.joinToString("\n") { it.first }, "recruit:" + ids.joinToString(","), lineH,
            0, 0, rows = rows)
    }

    /**
     * 세로 두 글자 메뉴('基/建', '终/端')에서 한 글자만 읽혀 별칭으로 찾았으면, 못 읽은 글자 자리까지 상자를 넓힌다.
     * 한 글자 상자의 위(못 읽은 글자가 앞) 또는 아래(뒤)로 한 칸.
     */
    private fun stretched(box: RectF, text: String, hit: Matcher.Hit): RectF {
        val t = Text.normalize(Text.simplify(text)).trimEnd('.')
        val zh = Text.normalize(hit.zh)
        if (hit.how != "alias" || t.length != 1 || zh.length != 2 || box.height() > box.width() * 1.6f) return RectF(box)
        val h = box.height()
        return when (t[0]) {
            zh[1] -> RectF(box.left, box.top - h * 1.1f, box.right, box.bottom)
            zh[0] -> RectF(box.left, box.top, box.right, box.bottom + h * 1.1f)
            else -> RectF(box)
        }
    }

    /** ML Kit으로 읽은 줄. 번역 상자에 걸친 줄은 상자 밖 낱말만 이어서 다시 줄로 만든다. */
    private fun readLines(bmp: Bitmap, covered: List<RectF>): List<OcrLine> {
        val result = Tasks.await(recognizer.process(InputImage.fromBitmap(bmp, 0)))
        val out = mutableListOf<OcrLine>()
        for (block in result.textBlocks) for (line in block.lines) {
            val lb = line.boundingBox?.let { RectF(it) } ?: continue
            var text = StringBuilder()
            var box: RectF? = null
            fun flush() {
                val b = box
                if (text.isNotBlank() && b != null) out += OcrLine(text.toString(), b)
                text = StringBuilder()
                box = null
            }
            for (e in line.elements) {
                val eb = e.boundingBox?.let { RectF(it) } ?: continue
                if (covered.any { it.contains(eb.centerX(), eb.centerY()) }) {
                    flush()
                    continue
                }
                // 한 줄 안에서 멀리 떨어진 낱말은 다른 글 (대사창의 이름 '阿米娅'와 대사)
                val prev = box
                if (prev != null && eb.left - prev.right > max(prev.height(), eb.height()) * 2.5f) flush()
                // 영문·숫자 낱말 사이는 띄어 쓴다 (중국어 낱말은 붙임)
                if (text.isNotEmpty() && text.last().isLetterOrDigit() && text.last().code < 128 &&
                    e.text.first().isLetterOrDigit() && e.text.first().code < 128
                ) text.append(' ')
                text.append(e.text)
                box = box?.apply { union(eb) } ?: eb
            }
            if (line.elements.isEmpty() && covered.none { RectF.intersects(it, lb) }) out += OcrLine(line.text, lb)
            flush()
        }
        return joinRow(out)
    }

    /**
     * ML Kit이 한 줄을 둘로 나눠 읽은 것(스토리 내레이션 등)을 다시 잇는다:
     * 높이가 비슷하고 세로로 대부분 겹치며, 앞 조각 끝과 뒤 조각 시작이 거의 붙어 있으면 한 줄.
     */
    private fun joinRow(lines: List<OcrLine>): List<OcrLine> {
        val rest = lines.sortedBy { it.box.left }.toMutableList()
        val out = mutableListOf<OcrLine>()
        while (rest.isNotEmpty()) {
            var cur = rest.removeAt(0)
            while (true) {
                val h = cur.box.height()
                val i = rest.indexOfFirst { b ->
                    val bh = b.box.height()
                    val overlap = min(cur.box.bottom, b.box.bottom) - max(cur.box.top, b.box.top)
                    max(h, bh) <= min(h, bh) * 1.4f && overlap >= min(h, bh) * 0.6f &&
                        b.box.left >= cur.box.right - min(h, bh) * 0.3f && b.box.left - cur.box.right <= min(h, bh) * 0.8f
                }
                if (i < 0) break
                val b = rest.removeAt(i)
                cur = OcrLine(cur.text + b.text, RectF(cur.box).apply { union(b.box) })
            }
            out += cur
        }
        return out
    }

    fun close() = recognizer.close()

    companion object {
        private const val TAG = "AkkrOverlay"
    }
}
