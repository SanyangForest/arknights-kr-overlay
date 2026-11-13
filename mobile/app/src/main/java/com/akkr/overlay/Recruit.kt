package com.akkr.overlay

/**
 * 공개모집 태그 조합 계산 (PC판 overlay/recruit.py와 같은 규칙). 데이터는 dict.db의 recruit_* 표 (build_recruit.py → export_dict.py).
 *
 * 모집 시간 9시간 기준으로, 화면의 태그 5개 중 1~3개를 고른 조합마다 나올 수 있는 오퍼레이터를 구해
 * 가장 낮은 등급이 4★ 이상인 조합(=4★ 이상 확정)을 알려 준다.
 *  - 6★는 '고급 특별 채용'을 골랐을 때만 나오고, 그때는 6★ 확정. '특별 채용'만 고르면 5★ 확정
 *  - '로봇'은 3:50 이하로 두면 1★ 로봇 확정 (9시간에는 1★가 안 나오므로 따로 알림)
 */
class Recruit(
    private val tags: Map<Int, Pair<String, String>>,  // id → (중국어, 한국어)
    private val ops: List<Op>,
    private val times: List<IntArray>,                // [시작 분, 최저 등급, 최고 등급]
    private val special: Map<Int, Set<Int>>,          // 특별 태그 id → 나오는 등급
) {
    class Op(val id: String, val ko: String, val rarity: Int, val tags: Set<Int>)
    class Combo(val tags: List<Int>, val low: Int, val ops: List<Op>)

    private val byZh = tags.entries.associate { it.value.first to it.key }

    /** OCR 글자들 중 태그 이름과 정확히 같은 것의 id (나온 순서, 중복 없이). */
    fun tagIds(texts: List<String>): List<Int> = texts.mapNotNull { byZh[it] }.distinct()

    fun isTag(text: String) = text in byZh

    fun ko(tag: Int) = tags[tag]?.second ?: "?"

    private fun rarityRange(minutes: Int): IntRange {
        var range = times.first().let { it[1]..it[2] }
        for (t in times) if (minutes >= t[0]) range = t[1]..t[2]
        return range
    }

    /** 이 조합으로 나올 수 있는 오퍼레이터 (등급 높은 순). */
    fun candidates(combo: Set<Int>, minutes: Int = NINE_HOURS): List<Op> {
        var allowed: Set<Int> = rarityRange(minutes).toSet()
        // 특별 태그는 그 등급만 나오게 한다 (고급 특별 채용 → 6★, 특별 채용 → 5★)
        for (tag in listOf(TOP, SENIOR)) if (tag in combo) {
            allowed = special[tag] ?: emptySet()
            break
        }
        return ops.filter { it.tags.containsAll(combo) && it.rarity in allowed }
            .sortedWith(compareBy({ -it.rarity }, { it.ko }))
    }

    /** 최저 등급이 minRarity 이상인 조합만, 좋은 순. 더 적은 태그로 같은 결과가 나오는 조합은 뺀다. */
    fun combos(ids: List<Int>, minutes: Int = NINE_HOURS, minRarity: Int = 4): List<Combo> {
        val tagIds = ids.distinct().filter { it in tags }
        val out = mutableListOf<Combo>()
        for (n in 1..3) for (combo in choose(tagIds, n)) {
            val found = candidates(combo.toSet(), minutes)
            if (found.isEmpty()) continue
            val low = found.minOf { it.rarity }
            if (low < minRarity) continue
            val idsOf = found.map { it.id }
            if (out.any { c -> combo.containsAll(c.tags) && c.tags.size < combo.size && c.ops.map { it.id } == idsOf }) continue
            out += Combo(combo, low, found)
        }
        return out.sortedWith(compareBy({ -it.low }, { it.ops.size }, { it.tags.size }))
    }

    /** '로봇' 태그가 있으면 3:50으로 1★ 로봇이 확정되는지. */
    fun robot(ids: List<Int>): Boolean =
        ROBOT in ids && candidates(setOf(ROBOT), ROBOT_TIME).let { c -> c.isNotEmpty() && c.all { it.rarity == 1 } }

    /** 화면 패널에 쓸 줄들: (글, 등급). 확정 조합이 없으면 그렇다고 한 줄. */
    fun describe(ids: List<Int>, maxRows: Int = 6, maxNames: Int = 5): List<Pair<String, Int>> {
        val rows = mutableListOf<Pair<String, Int>>()
        if (robot(ids)) rows += "로봇 → 1★ 확정 (3:50)" to 1
        for (c in combos(ids).take(maxRows)) {
            val names = c.ops.take(maxNames).joinToString(", ") { it.ko } + if (c.ops.size > maxNames) " …" else ""
            rows += "${c.tags.joinToString(" + ") { ko(it) }} → ${c.low}★: $names" to c.low
        }
        if (rows.isEmpty()) rows += "4★ 확정 조합 없음" to 3
        return rows
    }

    companion object {
        const val NINE_HOURS = 540
        const val ROBOT_TIME = 230  // 3:50
        const val SENIOR = 14       // 특별 채용
        const val TOP = 11          // 고급 특별 채용
        const val ROBOT = 28
        const val TITLE = "공개모집 확정 조합 (9시간)"

        private fun <T> choose(items: List<T>, n: Int): List<List<T>> =
            if (n == 0) listOf(emptyList())
            else items.indices.flatMap { i -> choose(items.drop(i + 1), n - 1).map { listOf(items[i]) + it } }

        /**
         * rows(sql): 각 줄의 칸을 글자로 돌려주는 조회 함수 (기기의 DictStore와 PC 시험의 JDBC가 같은 코드로 읽게).
         * 표가 없는 옛 사전이면 null.
         */
        fun load(rows: (String) -> List<List<String>>): Recruit? = runCatching {
            val tags = rows("SELECT id, zh, ko FROM recruit_tags").associate { it[0].toInt() to (it[1] to it[2]) }
            val ops = rows("SELECT id, ko, rarity, tags FROM recruit_ops").map {
                Op(it[0], it[1], it[2].toInt(), it[3].split(',').filter(String::isNotEmpty).map(String::toInt).toSet())
            }
            val rule = rows("SELECT k, v FROM recruit_rule").associate { it[0] to it[1] }
            val times = rule.getValue("times").lines().map { l -> l.split(',').map(String::toInt).toIntArray() }
            val special = rule.getValue("special").lines().associate { l ->
                val (k, v) = l.split('=')
                k.toInt() to v.split(',').map(String::toInt).toSet()
            }
            if (tags.isEmpty() || ops.isEmpty() || times.isEmpty()) null else Recruit(tags, ops, times, special)
        }.getOrNull()
    }
}
