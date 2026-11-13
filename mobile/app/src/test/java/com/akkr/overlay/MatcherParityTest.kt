package com.akkr.overlay

import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.sql.Connection
import java.sql.DriverManager

/**
 * PC에서 같은 dict.db를 JDBC로 읽는 사전 (기기에서는 DictStore가 안드로이드 SQLite로 읽음).
 * 안드로이드 rawQuery처럼 ? 인자를 모두 글자로 넘긴다 (정수 비교를 ?로 넘기면 기기에서만 틀리는 것을 여기서도 잡게).
 */
class JdbcDict(path: String) : Dict {
    private val c: Connection = DriverManager.getConnection("jdbc:sqlite:$path")
    private fun upper(p: String) = p + '￿'

    private fun <T> query(sql: String, vararg args: Any, row: (java.sql.ResultSet) -> T): List<T> =
        c.prepareStatement(sql).use { st ->
            args.forEachIndexed { i, a -> st.setString(i + 1, a.toString()) }
            st.executeQuery().use { rs -> buildList { while (rs.next()) add(row(rs)) } }
        }

    override fun exact(key: String) = query("SELECT ko FROM entries WHERE key=?", key) { it.getString(1) }.firstOrNull()
    override fun firstWithPrefix(prefix: String) =
        query("SELECT key, ko FROM entries WHERE key>=? AND key<? ORDER BY key LIMIT 1", prefix, upper(prefix)) {
            it.getString(1) to it.getString(2)
        }.firstOrNull()
    override fun skelExact(key: String) = query("SELECT zh, ko FROM skel WHERE key=?", key) { it.getString(1) to it.getString(2) }.firstOrNull()
    override fun skelFirstWithPrefix(prefix: String) =
        query("SELECT key, zh, ko FROM skel WHERE key>=? AND key<? ORDER BY key LIMIT 1", prefix, upper(prefix)) {
            Triple(it.getString(1), it.getString(2), it.getString(3))
        }.firstOrNull()
    override fun bucket(head: String, minLen: Int, maxLen: Int, limit: Int) =
        query("SELECT key, ko FROM entries WHERE key>=? AND key<? AND length(key) BETWEEN $minLen AND $maxLen LIMIT $limit", head, upper(head)) {
            it.getString(1) to it.getString(2)
        }
    override fun skelBucket(head: String, minLen: Int, maxLen: Int, limit: Int) =
        query("SELECT key, zh, ko FROM skel WHERE key>=? AND key<? AND length(key) BETWEEN $minLen AND $maxLen LIMIT $limit", head, upper(head)) {
            Triple(it.getString(1), it.getString(2), it.getString(3))
        }
    override fun suffixBucket(tail: String, minLen: Int, maxLen: Int, limit: Int): List<Pair<String, String>> {
        val r = tail.reversed()
        return query(
            "SELECT r.key, e.ko FROM rev r JOIN entries e ON e.key = r.key WHERE r.rkey>=? AND r.rkey<? AND length(r.key) BETWEEN $minLen AND $maxLen LIMIT $limit",
            r, upper(r),
        ) { it.getString(1) to it.getString(2) }
    }
    override fun meta(k: String) = query("SELECT v FROM meta WHERE k=?", k) { it.getString(1) }.firstOrNull()
    override fun alias(ocr: String) = query(DictStore.ALIAS_SQL, ocr) { it.getString(1) }.firstOrNull()
    override fun levels(key: String) = query("SELECT sets FROM levels WHERE key=?", key) { it.getString(1) }.firstOrNull()
    override fun skelMulti(key: String) = query("SELECT 1 FROM skel_multi WHERE key=?", key) { it.getInt(1) }.isNotEmpty()
    override fun prefixAmbiguous(prefix: String) =
        query("SELECT count(*) FROM (SELECT 1 FROM entries WHERE key>=? AND key<? LIMIT 2)", prefix, upper(prefix)) { it.getInt(1) }.first() >= 2
    override fun skelPrefixAmbiguous(prefix: String) =
        query("SELECT count(*) FROM (SELECT 1 FROM skel WHERE key>=? AND key<? LIMIT 2)", prefix, upper(prefix)) { it.getInt(1) }.first() >= 2
}

/**
 * 휴대폰판 Matcher가 PC판과 같은 번역을 내는지: 녹화에서 실제로 읽힌 OCR 문구(cases.tsv, PC판 결과 포함)로 비교.
 * PC판에만 있는 규칙(로고, 화면별 별칭, 괄호 틀 등)으로 찾은 것은 따로 센다.
 */
class MatcherParityTest {
    @Test
    fun sameAsPc() {
        val db = File("src/main/assets/dict.db")
        val m = Matcher(JdbcDict(db.absolutePath))
        val core = setOf("exact", "prefix", "number", "confuse", "fuzzy", "fuzzy_number", "fuzzy_prefix", "icon", "alias", "rule", "logo")
        var same = 0; var coreTotal = 0; var coreSame = 0; var total = 0
        val diffs = mutableListOf<String>()
        javaClass.classLoader!!.getResourceAsStream("cases.tsv")!!.bufferedReader().forEachLine { line ->
            val (text, how, ko) = line.split('\t').let { Triple(it[0], it.getOrElse(1) { "" }, it.getOrElse(2) { "" }) }
            val got = m.lookup(text)?.ko?.replace('\n', ' ') ?: ""
            total++
            val ok = got == ko
            if (ok) same++
            if (how.isEmpty() || how in core) {
                coreTotal++
                if (ok) coreSame++ else diffs += "$how\t$text\tPC=$ko\tPHONE=$got"
            }
        }
        File("build/parity_diffs.tsv").writeText(diffs.joinToString("\n"))
        println("전체 $same/$total 같음, 핵심 규칙 $coreSame/$coreTotal 같음 (다른 것: build/parity_diffs.tsv)")
        assertTrue("핵심 규칙 일치율이 너무 낮음", coreSame >= coreTotal * 0.9)
    }

    /** 폰(ML Kit)에서 실제로 읽힌 문구들 (2026-10-31 폰 점검 기록) */
    @Test
    fun phoneReadings() {
        val m = Matcher(JdbcDict(File("src/main/assets/dict.db").absolutePath))
        val cases = listOf(
            "理智/210" to "이성/210",
            "感謝庆典" to null, "特別招募" to null, "0.视相博物館演化" to null,
            "新人向调组合包¥18" to null, "寻访现已开放、特定干员出率上升" to null,
            "师鐵完成" to null, "集戌战略" to null, "终" to "단말기", "建" to null, "好友档案" to "친구 · 파일",
            "你怎么又睡養了?起来起来,陪我去买点东西,快" to null,
            "《慈悲灯塔》14-23良夜将死幕间" to "<자비의 등대> 14-23 고요한 밤의 죽음 브릿지",
            "155古堡笔记MAX" to "155 고성의 노트 MAX", "进入主题》》" to null, "深蓝记录录仪" to null, "採素者的银凇止境," to null,
            "公开招募干员寻访采购中心" to null,
            "《冻土与火种》网页活动奖励发放" to null, "2026「感谢庆典」直播奖励" to "2026 「감사 축제」 방송 보상",
            "其12废都安保派驻" to null, "发电站1" to "발전소", "宿舍M0" to "숙소",
            "|2026「感谢庆典」直播奖励" to "2026 「감사 축제」 방송 보상", "|2026-10-26 25日后过期15000" to null,
            "利余10" to "남은 10", "聚台剂" to "중합제",
            "利余可放置角包:3" to "배치 가능 인원: 3",
            "乍战正常运行中" to "대리 작전 지휘 정상 진행 중",
            "快婕編队" to "빠른 편성", "凛御報灰" to "실버애쉬 더 레인프로스트", "赤刀明霄陈" to "첸 더 던스트릭", "第一蝙队" to "제1편성",
        )
        val misses = mutableListOf<String>()
        for ((text, want) in cases) {
            val got = m.lookup(text)
            println("$text → ${got?.how} ${got?.ko}")
            if (got == null || (want != null && got.ko != want)) misses += text
        }
        assertTrue("번역 못 한 것: $misses", misses.isEmpty())
    }

    /** 숫자 채우기 (2026-11-01 PC 점검: 이벤트 종료 시각, 화면에만 있는 단위, 잘린 설명문 변수, 비슷한 문장의 다른 날짜) */
    @Test
    fun numberFill() {
        val m = Matcher(JdbcDict(File("src/main/assets/dict.db").absolutePath))
        val cases = listOf(
            "作战结束时间2026/11/2203:59" to "작전 종료 시간 2026/11/22 03:59",
            "1天后解锁" to "1일 후 해금",
            "攻击造成法术伤害；遂成法术伤害时附带11%的灼燃损伤；停止攻击。" to "공격 시 마법 대미지를 줌; 마법 대미지를 줄 때 11%의 소각 손상을 추가로 줌",
            "被同波次的多个“簇火”命中时，后续“簇火”" to "같은 파동의 '떨기불' 여러 개에 맞으면 이후 '떨기불'의 피해가 ?로 감소",
        )
        val wrong = cases.mapNotNull { (text, want) -> m.lookup(text)?.ko.takeIf { it != want }?.let { "$text → $it" } ?: if (m.lookup(text) == null) "$text → null" else null }
        assertTrue("다르게 나온 것: $wrong", wrong.isEmpty())
        // 스킬 설명: '%'를 '96'으로 읽어도 레벨 수치로 채움 (태블릿 2026-11-04, 안젤리나 더 멜로우 위시 3스킬)
        val skill = m.lookup("立刻起飞,攻击力+3096,获得65%的对空庇护,攻击范围扩大,且周围8格视作予愿")?.ko.orEmpty()
        assertTrue("스킬 수치: $skill", skill.startsWith("즉시 이륙, 공격력 +30%, 65%의 대공 가호 획득") && "380%" in skill)
        // 재능: 앞부분만 보이면 잠재 단계마다 다른 뒤 숫자는 '?' (탈룰라 제2재능)
        val talent = m.lookup("在场时，全场敌人灼燃损伤爆发后5秒内，于可")?.ko.orEmpty()
        assertTrue("재능 수치: $talent", "5초 동안" in talent && "?" in talent)
        // OCR이 날짜를 뭉개 읽은 것은 비슷한 문장의 다른 날짜(2023/01/07)를 보여 주지 않는다
        val garbled = m.lookup("战结束时间：2026/11/22:0359")?.ko.orEmpty()
        assertTrue("다른 날짜가 나옴: $garbled", "2023" !in garbled)
    }
}
