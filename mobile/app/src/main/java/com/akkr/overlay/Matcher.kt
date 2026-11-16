package com.akkr.overlay

/**
 * OCR로 읽은 중국어를 사전에서 찾는다. PC판 overlay/matcher.py의 핵심 순서를 옮긴 것:
 *   별칭 → 규칙(요일) → 완전 일치 → 앞부분 일치 → 숫자 틀 → 헷갈리는 글자 → 비슷한 문장 → 비슷한 앞부분
 *   → 이름+숫자(스탯) → 앞·뒤 아이콘 글자 떼기
 * PC판의 로고·화면별 별칭·괄호 틀 같은 나머지 규칙은 아직 옮기지 않았다.
 */
class Matcher(private val dict: Dict) {

    data class Hit(val how: String, val zh: String, val ko: String)

    private val cache = object : LinkedHashMap<String, Hit?>(512, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, Hit?>?) = size > 2000
    }

    // 대사에 박사 이름이 들어가면 사전의 '{@nickname}' 자리로 바꿔 찾고 번역에 다시 넣는다 (PC판 settings.json)
    private val nickname = dict.meta("nickname").orEmpty()

    // 흘러가는 안내 문구 (대리 지휘 전투 하단 등). 화면 끝에서 잘린 조각도 전체 문장으로 번역 (PC판 marquee)
    private val marquee = dict.meta("marquee").orEmpty().split('\n').filter { it.isNotEmpty() }

    // 오퍼레이터 이름 (채용 결과 '직군 + 이름' 규칙: 이름 쪽이 오퍼레이터일 때만)
    private val opNames = dict.meta("op_names").orEmpty().split('\n').filter { it.isNotEmpty() }.toSet()
    private val opSub: Map<String, List<String>> by lazy {
        val m = HashMap<String, MutableList<String>>()
        for (n in opNames) if (n.length >= 3) for (i in n.indices) m.getOrPut(n.substring(0, i) + "?" + n.substring(i + 1)) { mutableListOf() } += n
        m
    }

    /**
     * 채용 결과의 '직군 + 이름' ('先锋芬', 직군 아이콘이 끼어 '先锋之芬'·'术师A杜林') → '뱅가드 팽' (PC판 _class_name).
     * 연출 중이라 직군 한 글자가 틀리기도 함 ('洗锋芬', '木师杜林'): 남은 글자로 직군이 하나로 정해질 때만.
     * 이름은 오퍼레이터여야 하고, 세 글자 이상이면 한 글자 오독까지 ('泡普泉' = 泡普卡).
     */
    private fun className(q: String): Hit? {
        if (opNames.isEmpty()) return null
        val m = CLASS_NAME.matchEntire(q) ?: return null
        val head = m.groupValues[1]
        val cls = CLASS_KO[head] ?: run {
            if (dict.exact(head) != null) return null  // 앞 두 글자가 낱말이면 직군 오독이 아님 ('医生凯尔希')
            val cands = CLASS_KO.keys.filter { (it[0] == head[0]) != (it[1] == head[1]) }
            if (cands.size != 1) return null
            CLASS_KO[cands[0]]
        }
        val name = m.groupValues[3]
        val found = if (name in opNames) name else if (name.length >= 3) {
            name.indices.flatMap { i -> opSub[name.substring(0, i) + "?" + name.substring(i + 1)].orEmpty() }.distinct().singleOrNull()
        } else null
        val ko = found?.let { dict.exact(it) } ?: return null
        return Hit("rule", q, "$cls $ko")
    }

    fun lookup(text: String): Hit? = synchronized(cache) {
        if (cache.containsKey(text)) return cache[text]
        val named = nickname.isNotEmpty() && nickname in text
        val t = PHONE_FIX.fold(Text.simplify(text)) { acc, (a, b) -> acc.replace(a, b) }
        var hit = find(if (named) t.replace(nickname, "{@nickname}") else t)
        // 경고 삼각형 아이콘이 '八'로 읽혀 앞에 붙은 문장 ('八检测到当前代理指挥作战…'): 못 찾았으면 떼고 다시 (PC판과 같음)
        if (hit == null && t.startsWith("八") && Text.hanCount(t) >= 6) hit = find(t.substring(1))
        // 날짜와 시각이 붙은 것 ('만료 시간 2026/11/2303:59'): OCR이 사이 공백을 빼먹음 (PC판 DATETIME_GLUED)
        if (hit != null && DATETIME_GLUED.containsMatchIn(hit.ko)) hit = hit.copy(ko = DATETIME_GLUED.replace(hit.ko, "$1 $2"))
        if (hit != null && "{@nickname}" in hit.ko) hit = hit.copy(ko = hit.ko.replace("{@nickname}", nickname))
        // OCR이 날짜와 시각 사이 공백을 빼먹어 번역에 '2026/11/2303:59'처럼 붙어 나온 것을 띄운다 (PC판과 같음)
        if (hit != null && DATE_TIME.containsMatchIn(hit.ko)) hit = hit.copy(ko = DATE_TIME.replace(hit.ko, "$1 $2"))
        cache[text] = hit
        hit
    }

    private fun find(text: String): Hit? {
        var q = Text.normalize(text)
        if (!Text.hasHan(q) || isNoise(q)) return null
        // 아이콘·체크칸이 한자 한 글자로 읽힌 것 ('日', '口', '回'): 홀로 있으면 번역하지 않음
        Text.LEADING_JUNK.replace(q, "").let { if (it.length == 1 && it[0] in Text.LONE_ICON) return null }
        // 시계 아이콘이 '?', 'Q', '0.' 등으로 읽혀 남은 날짜·시간 앞에 붙은 것 (Q5天, 0.4天 → 4天)
        ICON_NUM.matchEntire(q)?.let { m -> q = m.groupValues[1].ifEmpty { m.groupValues[2] } }
        // 공지 날짜 칸 '12 / 11月'이 '121月'로 붙어 읽힘: 1~12월이 아니면 번역하지 않는다 ('121월'이 떴음, PC판과 같음)
        MONTH_ONLY.matchEntire(q)?.let { m -> if ((m.groupValues[1].toIntOrNull() ?: 99) !in 1..12) return null }
        // 괄호로 감싼 제목 '【中坚甄选】': 안의 낱말을 찾아 괄호째 (앞부분 맞추기로 그 낱말이 든 긴 문장이 잡혔음)
        BRACKETED.matchEntire(q)?.let { m ->
            if (dict.exact(q) == null) {
                find(m.groupValues[2])?.let { h ->
                    if (h.how in BRACKET_OK) return Hit(h.how, q, m.groupValues[1] + h.ko + m.groupValues[3])
                }
                return null  // 괄호 제목만 있는 줄: 그 낱말로 시작하는 긴 문장(앞부분 맞추기)으로 옮기지 않는다
            }
        }
        // 숫자 하나가 시계 아이콘 등으로 앞에 붙은 제목 ('0限时复刻'): 뒤가 사전에 그대로 있을 때만
        DIGIT_HEAD.matchEntire(q)?.let { m ->
            val rest = m.groupValues[1]
            if (dict.exact(q) == null && rest[0] !in Text.UNIT_CHARS) {
                // 뒤가 사전에 그대로 있거나, 한 글자 오독·헷갈리는 글자로 찾히면 ('1行动结東' → 行动结束)
                dict.exact(rest)?.let { return Hit("icon", rest, it) }
                find(rest)?.let { h -> if (h.how in DIGIT_HEAD_OK) return h }
            }
        }
        // 앞에 붙은 영문 장식 낱말 ('requirement从信用商店收取1次信用' = 임무 칸의 'requirement' 꼬리표 + 임무)
        LATIN_WORD_HEAD.find(q)?.let { m ->
            val rest = q.substring(m.value.length)
            if (dict.exact(q) == null && Text.hanCount(rest) >= 2) find(rest)?.let { return it }
        }
        // 앞 글자(干 등)가 영문 몇 자로 읽힌 것 ('CF员寻访' → '员寻访'): 남은 글이 별칭에 있으면 그것으로
        LATIN_HEAD.find(q)?.let { m ->
            val rest = q.substring(m.value.length)
            if (dict.alias(q) == null && dict.alias(rest) != null) q = rest
        }
        dict.alias(q)?.let { zh ->
            // '='로 시작하면 그 뒤가 바로 번역 (PC판 ocr_aliases.tsv와 같음)
            return if (zh.startsWith("=")) Hit("alias", q, zh.substring(1)) else whole(Text.normalize(zh))?.copy(how = "alias")
        }
        // 기반시설 방 이름 옆 레벨 막대를 OCR이 '000'·'!'·'…'로 읽은 것 ('制造站000' → 制造站)
        ROOM_BARS.matchEntire(q)?.let { m ->
            if (dict.exact(q) == null) dict.exact(m.groupValues[1])?.let { return Hit("icon", m.groupValues[1], it) }
        }
        WEEKDAY.matchEntire(q)?.let { m ->
            if (dict.exact(q) == null) return Hit("rule", q, m.groupValues[1].map { WEEKDAY_KO[it] }.joinToString("·") + " 개방")
        }
        // 세 자리 넘는 날수('207天', '2091天')는 아이콘·옆 숫자가 붙어 읽힌 것 (오리지늄 207+, 알림 20 + 창고 1天)
        if (BIG_DAYS.matches(q)) return null
        // 배너의 가격표가 붙은 상품 이름 ('新人同调组合包¥18'): 가격을 떼고 찾은 뒤 다시 붙임
        PRICE.matchEntire(q)?.let { m ->
            if (dict.exact(q) == null && Text.hasHan(m.groupValues[1])) find(m.groupValues[1])?.let { return it.copy(ko = "${it.ko} ¥${m.groupValues[2]}") }
        }
        // 스토리 제목 줄 '《慈悲灯塔》14-23良夜将死幕间': 장 이름·작전 번호·이름·구분을 각각
        STORY_TITLE.matchEntire(q)?.let { m ->
            val (chapter, code, name, part) = m.destructured
            val chKo = dict.exact(chapter) ?: whole(chapter)?.ko
            val nameKo = dict.exact(name) ?: whole(name)?.takeIf { it.how != "fuzzy_prefix" }?.ko
            if (chKo != null && nameKo != null) {
                val partKo = if (part.isEmpty()) "" else " " + (dict.exact(part) ?: part)
                return Hit("rule", q, "<$chKo> $code $nameKo$partKo")
            }
        }
        // 앞의 잡음('|' 등)을 뗀 글로 본다
        val clean = Text.LEADING_JUNK.replace(q, "")
        bracketTemplate(clean)?.let { return it }
        EXPIRE.matchEntire(clean)?.let { m ->
            splitExpire(m.groupValues[1], m.groupValues[2], m.groupValues[3])?.let { (date, left) ->
                // 옆의 보상 개수가 붙어 읽힌 것('…过期15000')은 그대로 뒤에 둔다
                val extra = m.groupValues[4].let { if (it.isEmpty()) "" else " $it" }
                return Hit("rule", q, "$date ${left}일 후 유효기간 만료$extra")
            }
        }
        // 이벤트 화면 '作战结束时间 2026/11/22 03:59': 정규화로 공백이 빠져 날짜와 시각이 붙음. 옆의 '剩余17天'까지 한 줄로 읽기도 함 (PC판 END_TIME)
        END_TIME.matchEntire(clean)?.let { m ->
            val (op, y, mo, d, hh, mm, left) = m.destructured
            return Hit("rule", q, "${if (op.isNotEmpty()) "작전 " else ""}종료 시간 $y/$mo/$d $hh:$mm" + (if (left.isNotEmpty()) "  ${left}일 남음" else ""))
        }
        // '이름/숫자' (홈의 '理智/210')
        LABEL_FRACTION.matchEntire(q)?.let { m ->
            dict.exact(m.groupValues[1])?.let { return Hit("number", m.groupValues[1], "$it/${m.groupValues[2]}") }
        }
        val stripped = Text.LEADING_JUNK.replace(q, "")
        var hit = if (Text.hasHan(stripped)) whole(stripped) else null
        // 이름만 사전에 그대로 있으면 '이름 숫자' (비슷한 문장 '法术抗性+15'로 찾으면 부호가 붙어 버림)
        LABEL_NUM.matchEntire(stripped)?.let { m ->
            val ko = dict.exact(m.groupValues[1])
            if (ko != null && (hit == null || hit!!.how.startsWith("fuzzy"))) hit = Hit("number", m.groupValues[1], "$ko ${m.groupValues[2]}")
        }
        if (hit == null && stripped != q) hit = whole(q)
        // 아이콘 규칙보다 먼저: '先锋芬'의 '芬'을 끝에 붙은 아이콘으로 떼어 버리지 않게
        if (hit == null) hit = className(stripped)
        // 앞의 아이콘이 한자로 잘못 읽힌 경우 (三攻击速度, 图较慢): 첫 글자를 빼고 완전 일치만
        if (hit == null && (stripped.length >= 4 || (stripped.length >= 3 && stripped[0] in Text.ICON_CHARS))) {
            val rest = stripped.substring(1)
            dict.exact(rest)?.let { hit = Hit("icon", rest, it) }
        }
        // 뒤의 아이콘이 한자로 읽힌 경우 (专精金): 끝 글자를 빼고 완전 일치만
        if (hit == null && stripped.length >= 3 && stripped.last() !in PARTICLES) {
            val rest = stripped.dropLast(1).trimEnd()
            dict.exact(rest)?.let { hit = Hit("icon", rest, it) }
        }
        if (hit == null) hit = logo(LOGO_JUNK.replace(stripped, "").trimEnd('.', ',', ':', ';', '!', '?'))
        // 두 문구가 '、'·','로 붙어 한 줄로 읽힌 것 ('寻访现已开放、特定干员出率上升'): 둘 다 사전에 있으면 각각 번역
        if (hit == null && (',' in stripped || '、' in stripped)) hit = joined(stripped)
        if (hit == null) hit = runs(stripped)
        if (hit == null && stripped.length >= 3 && marquee.isNotEmpty()) {
            // 헷갈리는 글자 하나는 바꿔 봄 (乍战 → 作战)
            val variants = setOf(stripped) + stripped.indices.flatMap { i ->
                Text.CONFUSE[stripped[i]].orEmpty().filter { it != stripped[i] }.map { c -> stripped.substring(0, i) + c + stripped.substring(i + 1) }
            }
            marquee.firstOrNull { full -> variants.any { it in full } }?.let { full -> dict.exact(full)?.let { hit = Hit("marquee", full, it) } }
        }
        return hit
    }

    /**
     * 띄어쓰기 없이 붙어 읽힌 메뉴 ('公开招募干员寻访采购中心'): 사전에 있는 2~6글자 단어로 빈틈없이 나눈다 (가장 적은 조각, 4조각까지).
     * PC판 _split_words와 같음. 번역 목록 또는 null.
     */
    private fun splitWords(run: String, maxWord: Int = 6, maxParts: Int = 4): List<String>? {
        val n = run.length
        if (n < 4) return null
        val best = arrayOfNulls<List<String>>(n + 1)
        best[0] = emptyList()
        for (i in 0 until n) {
            val cur = best[i] ?: continue
            if (cur.size >= maxParts) continue
            for (j in i + 2..minOf(n, i + maxWord)) {
                val ko = dict.exact(run.substring(i, j)) ?: continue
                val prev = best[j]
                if (prev == null || prev.size > cur.size + 1) best[j] = cur + ko
            }
        }
        return best[n]?.takeIf { it.size >= 2 }
    }

    /**
     * 한자 덩어리마다 번역하고 숫자·영문·기호는 그대로 둔다 ('155古堡笔记MAX' → '155 고성의 노트 MAX', '进入主题>>').
     * 모든 한자 덩어리가 사전에 있을 때만.
     */
    private fun runs(q: String): Hit? {
        val parts = HAN_RUN.findAll(q).toList()
        if (parts.isEmpty()) return null
        val whole = parts.size == 1 && parts[0].value.length == q.length
        val sb = StringBuilder()
        var pos = 0
        var translated = 0
        for (m in parts) {
            if (m.value.length < 2) {
                // 숫자 바로 앞뒤의 한 글자는 아이콘이 한자로 읽힌 것 ('#'·시계 아이콘): 버린다
                val nextDigit = q.getOrNull(m.range.last + 1)?.isDigit() == true
                val prevDigit = q.getOrNull(m.range.first - 1)?.isDigit() == true
                if (!nextDigit && !prevDigit) return null
                val raw = q.substring(pos, m.range.first).trim()
                if (raw.isNotEmpty()) sb.append(raw).append(' ')
                pos = m.range.last + 1
                continue
            }
            val ko = (if (whole) null else dict.exact(m.value) ?: whole(m.value)?.takeIf { it.how != "fuzzy_prefix" && it.how != "fuzzy" }?.ko)
                ?: splitWords(m.value)?.joinToString(" ") ?: return null
            translated++
            val raw = q.substring(pos, m.range.first).trim()
            if (raw.isNotEmpty()) sb.append(raw).append(' ')
            sb.append(ko).append(' ')
            pos = m.range.last + 1
        }
        // 번역한 덩어리가 없으면(숫자·잡음뿐) 상자를 만들지 않음
        if (translated == 0) return null
        val tail = q.substring(pos).trim()
        if (tail.isNotEmpty()) sb.append(tail)
        return Hit("segment", q, sb.toString().trim())
    }

    /** 괄호 안 이름이 바뀌는 틀: 괄호 안을 {0}(앞 연도는 {1})로 바꿔 사전 틀을 찾고, 이름은 사전에 있으면 번역해 넣는다. */
    private fun bracketTemplate(q: String): Hit? {
        val m = BRACKET.find(q) ?: return null
        val open = m.value.first()
        val close = m.value.last()
        val name = m.groupValues[1]
        val nameKo = dict.exact(name) ?: name
        val keyed = q.replaceRange(m.range, "$open{0}$close")
        dict.exact(keyed)?.let { return Hit("template", keyed, it.replace("{0}", nameKo)) }
        YEAR_HEAD.find(keyed)?.let { y ->
            val key2 = "{1}" + keyed.substring(y.value.length)
            dict.exact(key2)?.let { return Hit("template", key2, it.replace("{0}", nameKo).replace("{1}", y.value)) }
        }
        return null
    }

    /**
     * 우편 유효기간에서 붙어 읽힌 받은 날짜와 남은 날수 나누기 ('2026-8-188' = 8-1 + 88일). PC판 split_expire와 같음:
     * (오늘 + 남은 날수) - 받은 날짜 = 보관 기간이 흔한 값(30일, 180일 …)에 가까운 쪽.
     */
    private fun splitExpire(y: String, mo: String, rest: String): Pair<String, String>? {
        val today = java.time.LocalDate.now()
        var best: Triple<Double, String, String>? = null
        for (k in listOf(2, 1)) {
            if (rest.length <= k) continue
            val day = rest.substring(0, k)
            val left = rest.substring(k)
            if (left.startsWith("0")) continue
            val got = runCatching { java.time.LocalDate.of(y.toInt(), mo.toInt(), day.toInt()) }.getOrNull() ?: continue
            if (got.isAfter(today.plusDays(1))) continue
            val total = java.time.temporal.ChronoUnit.DAYS.between(got, today) + left.toLong()
            val score = MAIL_VALIDITY.minOf { v -> kotlin.math.abs(total - v).toDouble() / v }
            if (best == null || score < best.first) best = Triple(score, "$y-$mo-$day", left)
        }
        return best?.let { it.second to it.third }
    }

    private fun joined(q: String): Hit? {
        val parts = q.split(',', '、').map { it.trim() }.filter { it.isNotEmpty() }
        if (parts.size !in 2..4 || parts.any { Text.hanCount(it) < 2 }) return null
        val kos = parts.map { p -> dict.exact(p) ?: whole(p)?.takeIf { it.how != "fuzzy_prefix" }?.ko ?: return null }
        return Hit("segment", q, kos.joinToString(", "))
    }

    /** 꾸민 글씨 로고 (통합 전략 테마, 이벤트 배너): OCR이 매번 다르게 읽어도 글자가 충분히 겹치면 같은 로고. */
    private fun logo(s: String): Hit? {
        if (s.length !in 3..8 || Text.hanCount(s) != s.length) return null
        val chars = s.toSet()
        val logos = LOGO_TITLES.filter { t ->
            val shared = (chars intersect t.toSet()).size
            s.last() in t && s.length <= t.length + 1 && dict.exact(t) != null &&
                ((s[0] in t || Text.CONFUSE[s[0]].orEmpty().any { it in t }) && shared >= 2 ||
                    s.length >= t.length - 2 && shared >= maxOf(3.0, t.length / 2.0))
        }
        return if (logos.size == 1) Hit("logo", logos[0], dict.exact(logos[0])!!) else null
    }

    /** 아이콘이 한자 한 글자로 읽히고 나머지는 숫자뿐인 것 (区835, 仓16). 번역할 것이 없음. */
    private fun isNoise(q: String): Boolean {
        val han = Text.HAN.findAll(q).map { it.value[0] }.toList()
        if (han.size != 1 || !(q.first() == han[0] || q.last() == han[0]) || !Text.DIGITS.containsMatchIn(q)) return false
        return han[0] in Text.NOISE_CHARS ||
            (han[0] !in Text.UNIT_CHARS && q.first() == han[0] && q.substring(1).all { it.isDigit() })
    }

    fun whole(q: String): Hit? {
        dict.exact(q)?.let { return Hit("exact", q, it) }
        if (q.length >= MIN_PREFIX) {
            dict.firstWithPrefix(q)?.let { (k, ko) ->
                if (Text.digits(k).take(Text.digits(q).size) == Text.digits(q)) {
                    // 같은 앞부분으로 시작하는 문장이 여럿이면(재능 잠재 단계별 수치) 안 보이는 뒤 숫자는 '?' (PC판과 같음)
                    val out = when {
                        PLACEHOLDER.containsMatchIn(ko.replace("{@nickname}", "")) -> fillVars(k, ko, q)
                        k != q && Text.NUMBER.containsMatchIn(ko) && dict.prefixAmbiguous(q) -> fill(k, ko, q, ambiguous = true)!!
                        else -> ko
                    }
                    return Hit("prefix", k, out)
                }
            }
        }
        val sq = Text.skeleton(q)
        if ('#' in sq && Text.hasHan(sq)) {
            val exact = dict.skelExact(sq)
            val t = exact ?: (if (sq.length >= MIN_PREFIX) dict.skelFirstWithPrefix(sq)?.let { it.second to it.third } else null)
            // 앞부분만 맞는 틀이 둘 이상이면(재능 잠재 단계별 '4(+1)초' 등) 안 보이는 숫자는 어느 것인지 모름 (PC판과 같음)
            if (t != null) return Hit("number", t.first, fill(t.first, t.second, q, ambiguous = exact == null && dict.skelPrefixAmbiguous(sq))!!)
        }
        // 헷갈리는 글자 교정 (한 글자만 바꿔 보고, 후보가 하나일 때만). 한 글자짜리는 안 함 (홈 로고 '干' → 于 '에')
        if (q.length in 2..12) {
            val variants = q.indices.flatMap { i -> Text.CONFUSE[q[i]].orEmpty().filter { it != q[i] }.map { c -> q.substring(0, i) + c + q.substring(i + 1) } }.toSet()
            val found = variants.mapNotNull { v -> dict.exact(v)?.let { v to it } }
            if (found.size == 1) return Hit("confuse", found[0].first, found[0].second)
            // 숫자 틀로 맞는 것 ('利余10' → 剩余{0} → 남은 10)
            if (found.isEmpty()) {
                val byNum = variants.mapNotNull { v -> Text.skeleton(v).takeIf { '#' in it }?.let { dict.skelExact(it) }?.let { v to it } }
                if (byNum.size == 1) return Hit("confuse", byNum[0].second.first, fill(byNum[0].second.first, byNum[0].second.second, byNum[0].first)!!)
            }
        }
        // 두 문구가 한 줄로 붙어 읽힘 ('寻访现已开放' + '特定干员出率上升', 배너 두 줄): 둘 다 사전에 그대로 있을 때만 (PC판과 같음)
        if (q.length in 6..40) {
            for (i in 3..q.length - 3) {
                val a = q.substring(0, i)
                val b = q.substring(i)
                if (!Text.hasHan(a) || !Text.hasHan(b)) continue
                val ka = dict.exact(a) ?: continue
                val kb = dict.exact(b) ?: continue
                return Hit("split", q, "$ka $kb")
            }
        }
        oneChar(q)?.let { return it }
        fuzzy(q)?.let { return it }
        fuzzyNumber(q)?.let { return it }
        fuzzyPrefix(q, 88)?.let { (key, ko) ->
            // 짧은 글이 아주 긴 문장의 앞부분으로 잡히면 엉뚱한 설명문이 나옴
            if (!(q.length < 10 && key.length > 2 * q.length) &&
                Text.digits(key).take(Text.digits(q).size) == Text.digits(q)
            ) return Hit("fuzzy_prefix", key, fillVars(key, ko, q))
        }
        // 숫자 틀의 비슷한 앞부분 (스킬 설명 첫 줄에 OCR 오타: '获得' → '狭得'). 레벨 수치로 채운다 (PC판 _fuzzy_prefix의 skel 쪽)
        fuzzySkelPrefix(q, 88)?.let { (zh, ko) ->
            if (!(q.length < 10 && zh.length > 2 * q.length)) fill(zh, ko, q, strict = true)?.let { return Hit("fuzzy_prefix", zh, it) }
        }
        return null
    }

    /**
     * 짧은 문구의 한 글자 오독 ('快婕编队', '凛御报灰', '赤刀明霄陈'): 비슷한 문장 찾기에는 너무 짧으므로,
     * 길이가 같고 한 글자만 다른 사전 문구가 딱 하나일 때만 그것으로 본다.
     */
    private fun oneChar(q: String): Hit? {
        if (q.length !in 4..8 || Text.hanCount(q) < q.length - 1) return null
        // 앞뒤의 아이콘이 한자로 읽힌 것('十生命值', '狼诗怀雅')은 떼고 찾는 쪽(icon)이 맞다
        if (dict.exact(q.substring(1)) != null || dict.exact(q.dropLast(1)) != null) return null
        val cands = (dict.bucket(q.take(2), q.length, q.length, 500) + dict.suffixBucket(q.takeLast(2), q.length, q.length, 500))
            .distinctBy { it.first }
            .filter { (k, _) ->
                val diff = k.indices.filter { k[it] != q[it] }
                // 다른 글자는 한자끼리 (문장부호·숫자가 다르면 다른 문구)
                k.length == q.length && diff.size == 1 && Text.hasHan(k[diff[0]].toString()) && Text.hasHan(q[diff[0]].toString())
            }
        return if (cands.size == 1) Hit("one_char", cands[0].first, cands[0].second) else null
    }

    /** 비슷한 문장 (OCR 오타). 첫 두 글자(헷갈리는 글자로 바꾼 것 포함)가 같은 사전 문장 중에서 찾는다. */
    private fun fuzzy(q: String): Hit? {
        if (q.length < FUZZY_MIN_LEN) return null
        val span = maxOf(2, q.length / 5)
        var best: Pair<String, String>? = null
        var bestScore = FUZZY_CUTOFF - 1
        // 첫 두 글자가 같은 후보 + 끝 두 글자가 같은 후보 (첫 글자를 틀리게 읽거나 잘린 OCR: '定干员出率上升')
        // 맨 앞 말줄임표를 OCR이 빼먹은 대사도 ('……要是…' → '要是…', 사전 키는 '.要是…')
        val cands = heads(q, 2).flatMap { dict.bucket(it, q.length - span, q.length + span) + dict.bucket(".$it", q.length - span, q.length + span + 1) } +
            dict.suffixBucket(q.takeLast(2), q.length - span, q.length + span)
        for ((k, ko) in cands) {
            val s = Text.ratio(q, k)
            if (s > bestScore) { bestScore = s; best = k to ko }
        }
        val (k, ko) = best ?: return null
        // OCR 문구가 사전 문장보다 꽤 길면 뒷부분 뜻이 빠지므로 쓰지 않음. 숫자·한자 숫자가 다르면 다른 문장
        if ((k.length < q.length * 0.85 && k.length < q.length - 1) || Text.digits(k) != Text.digits(q) || Text.cnNums(k) != Text.cnNums(q)) return null
        return Hit("fuzzy", k, ko)
    }

    /** 숫자가 든 짧은 문구의 OCR 오타 ('剩余气收置角色：9'): 숫자를 #로 바꾼 틀끼리 비교 */
    private fun fuzzyNumber(q: String): Hit? {
        val sq = Text.skeleton(q)
        if ('#' !in sq || sq.length < FUZZY_MIN_LEN) return null
        val span = maxOf(1, sq.length / 5)
        val cutoff = if (sq.length <= 12) 75 else FUZZY_CUTOFF
        var best: Triple<String, String, String>? = null
        var bestScore = cutoff - 1
        for (head in heads(sq, 2)) {
            for (t in dict.skelBucket(head, sq.length - span, sq.length + span)) {
                val s = Text.ratio(sq, t.first)
                if (s > bestScore) { bestScore = s; best = t }
            }
        }
        val t = best ?: return null
        if (bestScore < FUZZY_CUTOFF) {
            val a = sq.replace("#", ""); val b = t.first.replace("#", "")
            if (a.isEmpty() || b.isEmpty() || a[0] != b[0] || a.takeLast(2) != b.takeLast(2)) return null
        }
        if (sq.count { it == '#' } != t.first.count { it == '#' } || Text.cnNums(t.first) != Text.cnNums(sq)) return null
        return fill(t.second, t.third, q, strict = true)?.let { Hit("fuzzy_number", t.second, it) }
    }

    /** 비슷한 앞부분: 줄바꿈된 긴 문장의 첫 줄에 OCR 오타가 섞인 것. (키, 번역) */
    fun fuzzyPrefix(q: String, cutoff: Int = 85): Pair<String, String>? {
        if (q.length < FUZZY_PREFIX_MIN_LEN) return null
        var best: Pair<String, String>? = null
        var bestScore = cutoff - 1
        val hs = heads(q, 4).toMutableSet()
        hs += q.substring(0, 3)
        // 머리의 말줄임표를 OCR이 빼먹은 대사 ('嗯……博士是' → '嗯博士是')
        for (h in heads(q, 4)) for (i in 1..2) hs += h.substring(0, i) + "." + h.substring(i, 3)
        // 대사 맨 앞의 말줄임표를 OCR이 빼먹은 것 ('……要是在这里' → '要是在这里', 사전 키는 '.要是…')
        for (h in heads(q, 3)) hs += "." + h
        for (head in hs) {
            for ((k, ko) in dict.bucket(head, q.length - 2, 400, limit = 1500)) {
                val s = Text.ratio(q, k.take(q.length))
                if (s > bestScore) { bestScore = s; best = k to ko }
            }
        }
        return best
    }

    /** 숫자를 #로 바꾼 틀 중 앞부분이 q의 틀과 가장 비슷한 것: (원문 틀, 번역 틀). */
    private fun fuzzySkelPrefix(q: String, cutoff: Int): Pair<String, String>? {
        val sq = Text.skeleton(q)
        if ('#' !in sq || sq.length < FUZZY_PREFIX_MIN_LEN) return null
        var best: Pair<String, String>? = null
        var bestScore = cutoff - 1
        val hs = heads(sq, 4).toMutableSet()
        hs += sq.substring(0, 3)
        for (head in hs) {
            for ((k, zh, ko) in dict.skelBucket(head, sq.length - 2, 400, limit = 1500)) {
                val s = Text.ratio(sq, k.take(sq.length))
                if (s > bestScore) { bestScore = s; best = zh to ko }
            }
        }
        return best
    }

    /** 앞 n글자와, 그중 한 글자를 헷갈리는 글자로 바꾼 머리들 */
    private fun heads(q: String, n: Int): Set<String> {
        val h = q.take(n)
        return setOf(h) + h.indices.flatMap { i -> Text.CONFUSE[h[i]].orEmpty().filter { it != h[i] }.map { c -> h.substring(0, i) + c + h.substring(i + 1) } }
    }

    /** text가 사전의 어떤 문장의 앞부분인가 (줄바꿈된 문장 합치기 판단용). fuzzy면 OCR 오타는 조금 허용. */
    fun canContinue(text: String, fuzzy: Boolean = true): Boolean {
        val q = Text.LEADING_JUNK.replace(Text.normalize(Text.simplify(text)), "")
        if (q.isEmpty()) return false
        if (dict.firstWithPrefix(q) != null || dict.skelFirstWithPrefix(Text.skeleton(q)) != null) return true
        return fuzzy && fuzzyPrefix(q) != null
    }

    /** 사전에 그대로 있는 완결된 문구인가 (따로 완결된 두 줄을 붙이지 않으려고). */
    fun isEntry(text: String): Boolean {
        val q = Text.normalize(Text.simplify(text))
        if (dict.exact(q) != null) return true
        val s = Text.LEADING_JUNK.replace(q, "")
        return dict.exact(s) != null || (s.length >= 3 && s[0] in Text.ICON_CHARS && dict.exact(s.substring(1)) != null)
    }

    /** text가 긴 문장 fullZh의 끝부분인가 (줄바꿈된 마지막 줄). */
    fun isEndOf(text: String, fullZh: String): Boolean {
        val q = Text.normalize(Text.simplify(text)).trimEnd('.', '!', '?', '"')
        val z = Text.normalize(fullZh).trimEnd('.', '!', '?', '"')
        return Text.hasHan(q) && z.length > q.length + 6 && z.endsWith(q)
    }

    /** text가 윗줄에서 이미 통째로 번역한 문장(fullZh)의 일부인가 (줄바꿈된 꼬리 줄). */
    fun isTailOf(text: String, fullZh: String): Boolean {
        val q = Text.normalize(Text.simplify(text))
        val full = Text.normalize(fullZh)
        if (!Text.hasHan(q)) return q.length >= 2 && q.any { !it.isLetterOrDigit() } && full.endsWith(q)
        // 한 글자만 남은 마지막 줄 ('…今天想买点什么' + '吗？'): 윗문장이 그것으로 끝날 때만 (PC판과 같음)
        // 짧은 세로 로고('终/端')는 아래 한 글자만 따로 읽혀도 꼬리 (PC판과 같음)
        if (Text.hanCount(q) < 2) return (q.length >= 2 || full.length <= 4) && full.endsWith(q)
        if (full.contains(q)) return true
        // 숫자·변수를 #로 바꿔 비교 (사전 문장은 '{n}', 화면은 '3'). OCR 오타가 섞인 꼬리 줄('颧外' ↔ '额外')은
        // 같은 길이 구간과 아주 비슷하면 꼬리 (PC판 is_tail_of)
        val sq = Text.skeleton(q)
        val sFull = Text.skeleton(full)
        if (sFull.contains(sq)) return true
        if (Text.hanCount(q) < TAIL_FUZZY_MIN) return false
        return (0..maxOf(0, sFull.length - sq.length)).any { i -> Text.ratio(sq, sFull.substring(i, minOf(sFull.length, i + sq.length))) >= TAIL_FUZZY_CUTOFF }
    }

    private fun parseLevels(sets: String): List<Map<String, Double>> = sets.split('\n').map { line ->
        line.split(',').filter { '=' in it }.associate { it.substringBeforeLast('=') to it.substringAfterLast('=').toDouble() }
    }

    /**
     * 스킬 설명: 게임 데이터의 레벨별 수치 중 화면 숫자와 맞는 레벨로 채운다 (화면 숫자는 레벨을 고르는 데만 씀, PC판 _fill_levels).
     * 보이는 숫자로 레벨을 하나로 못 좁히면 레벨마다 다른 자리만 '?'. 보이는 숫자가 어느 레벨과도 안 맞으면 null.
     */
    private fun fillLevels(zh: String, ko: String, q: String, sets: List<Map<String, Double>>): String? {
        val pairs = TOKEN.findAll(zh).map { it.value }.toList().zip(Text.NUMBER.findAll(q).map { it.value }.toList())
        var good = sets
        if (pairs.isNotEmpty()) {
            val scored = sets.map { vals ->
                var hit = 0
                var miss = 0
                for ((tok, got) in pairs) {
                    val want = (if (tok.startsWith("{")) formatValue(tok, vals) else tok) ?: continue
                    if (ocrMatches(want, got)) hit++ else miss++
                }
                Triple(miss, hit, vals)
            }
            val least = scored.minOf { it.first }
            val best = scored.filter { it.first == least }
            if (least > 0 && best.none { it.second > 0 }) return null
            good = best.map { it.third }
        }
        return PLACEHOLDER.replace(ko) { m ->
            if (m.value == "{@nickname}") return@replace m.value
            val found = good.mapNotNull { formatValue(m.value, it) }.toSet()
            if (found.size == 1) found.first() else "?"
        }
    }

    /** 줄이 잘려 앞부분만 보이는 스킬 설명: 번역 틀의 변수({same_wave_repeat_damage_scale:0%})를 날것으로 보이지 않게 (PC판 prefix) */
    private fun fillVars(zh: String, ko: String, q: String): String =
        if (PLACEHOLDER.containsMatchIn(ko.replace("{@nickname}", ""))) fill(zh, ko, q)!! else ko

    /**
     * 숫자 틀로 찾은 문장: zh의 숫자·변수 자리에 화면의 숫자를 넣어 ko를 만든다 (PC판 _fill).
     * strict: 원문에 그대로 쓰인 숫자를 화면 숫자로 바꿀 수 없으면 null (비슷한 문장의 다른 날짜·수치를 그대로 보여 주지 않게).
     */
    private fun fill(zh: String, ko: String, q: String, strict: Boolean = false, ambiguous: Boolean = false): String? {
        dict.levels(Text.normalize(zh))?.let { sets -> fillLevels(zh, ko, q, parseLevels(sets))?.let { return it } }
        val matches = Text.NUMBER.findAll(q).toList()
        val values = matches.map { m ->
            val before = q.getOrNull(m.range.first - 1); val after = q.getOrNull(m.range.last + 1)
            val keepZero = (before != null && before in ":/-") || (after != null && after in ":/") || '.' in m.value
            if (keepZero) m.value else m.value.trimStart('0').ifEmpty { "0" }
        }
        val tokens = TOKEN.findAll(zh).map { it.value }.toList()
        val named = HashMap<String, String>()
        val namedAt = HashMap<String, Int>()
        tokens.zip(values).forEachIndexed { i, (t, v) -> if (t.startsWith("{")) { named[t] = v; namedAt[t] = i } }
        val unitAfter = HashMap<String, String>()
        for (t in named.keys) {
            val after = zh.substring(zh.indexOf(t) + t.length)
            val unit = Text.UNITS.entries.firstOrNull { after.startsWith(it.key) }
                // 원문 틀에는 단위가 없고 화면 숫자 뒤에만 있는 것 ('1天后解锁' ↔ 틀 '{0}后解锁', 한섭 '{0} 후 개방')
                ?: q.substring(matches[namedAt[t]!!].range.last + 1).let { qa -> Text.UNITS.entries.firstOrNull { qa.startsWith(it.key) } }
            unit?.let { unitAfter[t] = it.value }
        }
        var out = PLACEHOLDER.replace(ko) { m ->
            if (m.value == "{@nickname}") return@replace m.value  // 박사 이름은 lookup이 넣는다
            var v = named[m.value] ?: "?"
            val nxt = ko.getOrNull(m.range.last + 1)
            val unit = unitAfter[m.value]
            if (unit != null && !(nxt != null && nxt in '가'..'힣')) v += unit
            v
        }
        // 원문에 그대로 쓰인 숫자가 화면과 다르면(같은 틀의 다른 수치) 순서대로 바꿈
        val literalZh = tokens.filter { !it.startsWith("{") }
        val seen = tokens.zip(values).filter { !it.first.startsWith("{") }
        val literalQ = seen.map { it.second }
        val differs = seen.map { it.first } != literalQ  // 화면에 보이는 만큼의 숫자가 원문과 다름
        val nOut = Text.NUMBER.findAll(out).count()
        if (differs && literalZh.size == literalQ.size && literalZh.size == nOut) {
            val vals = literalQ.iterator()
            out = Text.NUMBER.replace(out) { vals.next() }
        } else if (differs && strict) return null
        else if ((differs || ambiguous || dict.skelMulti(Text.skeleton(zh))) && !strict && literalQ.size < literalZh.size && literalZh.size == nOut) {
            // 앞부분만 보이는 문장 (재능 잠재 단계별 문장 등): 보이는 숫자는 화면 값, 안 보이는 뒤 숫자는 '?' (PC판과 같음)
            val vals = literalQ.iterator()
            out = Text.NUMBER.replace(out) { m -> if (vals.hasNext()) vals.next() else "?" + (if (m.value.endsWith("%")) "%" else "") }
        }
        return out
    }

    companion object {
        private const val MIN_PREFIX = 6
        private const val TAIL_FUZZY_MIN = 6
        private const val TAIL_FUZZY_CUTOFF = 75  // 바로 아래 줄일 때만 보므로(위치 조건) 조금 너그럽게
        private const val FUZZY_MIN_LEN = 5
        private const val FUZZY_PREFIX_MIN_LEN = 8
        private const val FUZZY_CUTOFF = 85
        private val PLACEHOLDER = Regex("\\{[^{}]*\\}")
        private val VAR = Regex("\\{(-)?([^:{}]+)(?::([^{}]*))?\\}")

        /** 스킬 설명 변수({atk:0%}, {-move_speed:0%}, {duration:0.0})를 게임처럼 숫자로 (PC판 format_value). */
        fun formatValue(token: String, vals: Map<String, Double>): String? {
            val m = VAR.matchEntire(token) ?: return null
            val raw = vals[m.groupValues[2].lowercase()] ?: return null
            var v = java.math.BigDecimal(raw.toString())
            if (m.groupValues[1].isNotEmpty()) v = v.negate()
            var fmt = m.groupValues[3]
            val pct = fmt.endsWith("%")
            if (pct) {
                v = v.multiply(java.math.BigDecimal(100))
                fmt = fmt.dropLast(1)
            }
            val text = if (fmt.isNotEmpty()) {
                v.setScale(if ('.' in fmt) fmt.substringAfter('.').length else 0, java.math.RoundingMode.HALF_UP).toPlainString()
            } else {
                val s = v.stripTrailingZeros()
                if (s.scale() <= 0) s.toBigInteger().toString() else s.toPlainString()
            }
            return text + if (pct) "%" else ""
        }

        /** 화면 숫자가 데이터 수치와 같은가. OCR이 '%'를 9·96으로 읽은 것도 같다고 본다 (+30% → +3096). */
        fun ocrMatches(expected: String, got: String): Boolean {
            val e = expected.trimEnd('%').trimStart('+', '-')
            val g = got.trimEnd('%').trimStart('+', '-')
            return g == e || (expected.endsWith("%") && g.startsWith(e) && g.substring(e.length) in setOf("9", "96", "6"))
        }

        private val TOKEN = Regex("\\{[^{}]*\\}|\\d+(?:\\.\\d+)?%?")
        private val PARTICLES = "了的吗呢吧啊呀哦嘛么着过".toSet()
        // 자원 수집 화면의 요일 표시 (周一二五六开放 → 월·화·금·토 개방)
        private val WEEKDAY = Regex("周([一二三四五六日]+)开放")
        private val WEEKDAY_KO = "一二三四五六日".zip("월화수목금토일").toMap()
        private val MONTH_ONLY = Regex("(\\d+)月")
        private val BRACKETED = Regex("([\\[<「\"])([^\\[\\]<>「」\"]{2,20})([\\]>」\"])")
        private val BRACKET_OK = setOf("exact", "alias", "title", "confuse", "one_char", "fuzzy", "icon")
        private val DIGIT_HEAD_OK = setOf("exact", "alias", "title", "confuse", "one_char", "fuzzy", "icon")
        private val DIGIT_HEAD = Regex("\\d([\\u4e00-\\u9fff]{2}.*)")
        // 둘째: 앞에 붙은 숫자 1~3자리 + 점 ('0.4天', 홈 창고의 알림 '!2' + '6天' = '02.6天')
        private val ICON_NUM = Regex("[A-Za-z¥?.]+(\\d+(?:天|小时|分钟|分|秒|日))|\\d{1,3}[.,](\\d+(?:天|日))")
        // 채용 결과 '직군 + (직군 아이콘) + 이름' (PC판 CLASS_KO / CLASS_NAME·CLASS_LOOSE)
        private val CLASS_KO = mapOf("先锋" to "뱅가드", "近卫" to "가드", "重装" to "디펜더", "狙击" to "스나이퍼",
            "术师" to "캐스터", "医疗" to "메딕", "辅助" to "서포터", "特种" to "스페셜리스트")
        private val CLASS_NAME = Regex("([\\u4e00-\\u9fff]{2})(之|A|Δ|△|图)?([\\u4e00-\\u9fff]{1,12})")
        private val ROOM_BARS = Regex("(.+?(?:站|室|舍|中枢))[0-9OIlMm|!.]+")
        // 꾸민 글씨 로고 (사전 키 모양: ：→:)
        private val LOGO_TITLES = listOf(
            "傀影与猩红孤钻", "水月与深蓝之树", "探索者的银凇止境", "萨卡兹的无终奇语", "岁的界园志异", "沉沦者的黑流树海", "视相博物馆:演化",
        )
        // 짧은 이름 바로 뒤의 숫자 (스탯 '法术抗性15')
        private val LABEL_NUM = Regex("([一-鿿]{2,6})(\\d+(?:\\.\\d+)?%?)")
        private val LABEL_FRACTION = Regex("([一-鿿]{2,6})/(\\d+)")
        private val STORY_TITLE = Regex("<(.+?)>([A-Z]{0,3}\\d{1,2}-\\d{1,2})(.+?)(幕间|行动前|行动后)?")
        private val HAN_RUN = Regex("[一-鿿]+")
        // 폰 OCR이 늘 같은 식으로 틀리는 두 글자 낱말 (숫자가 붙은 글이라 별칭표로는 못 잡음: '利余可放置角包:3')
        private val PHONE_FIX = listOf("利余" to "剩余", "角包" to "角色")
        private val BRACKET = Regex("[<「『]([^<>「」『』]{1,20})[>」』]")
        private val YEAR_HEAD = Regex("^\\d{4}")
        private val EXPIRE = Regex("(\\d{4})-(\\d{1,2})-(\\d{2,})日后过期(\\d*)")
        private val DATE_TIME = Regex("(\\d{4}/\\d{1,2}/\\d{2})(\\d{2}:\\d{2})")
        private val DATETIME_GLUED = Regex("(\\d{4}[/-]\\d{1,2}[/-]\\d{2})(\\d{2}:\\d{2})")
        private val END_TIME = Regex("(作战)?结[束東]时间:?(\\d{4})/(\\d{1,2})/(\\d{2})(\\d{2}):?(\\d{2})(?:剩?余(\\d*)天?)?\\W*")
        private val MAIL_VALIDITY = listOf(7, 10, 14, 15, 30, 60, 90, 180, 365)
        private val LATIN_HEAD = Regex("^[A-Za-z]{1,3}(?=[一-鿿])")
        private val LATIN_WORD_HEAD = Regex("^[A-Za-z][A-Za-z ._-]{3,}(?=[一-鿿])")
        private val BIG_DAYS = Regex("\\d{3,}[天日]")
        private val PRICE = Regex("(.+?)[¥￥](\\d+)")
        // 로고 앞에 붙은 장식이 '0.'·'O.'로 읽힌 것
        private val LOGO_JUNK = Regex("^[0-9A-Za-z][.,]")
    }
}
