package com.akkr.overlay

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File
import java.sql.DriverManager

/** 공개모집 조합 계산: PC판 overlay/test_recruit.py와 같은 사례. */
class RecruitTest {
    private val r: Recruit by lazy {
        val db = File("src/main/assets/dict.db")
        assumeTrue("dict.db 없음", db.exists())
        val c = DriverManager.getConnection("jdbc:sqlite:${db.absolutePath}")
        val loaded = Recruit.load { sql ->
            c.createStatement().use { st ->
                st.executeQuery(sql).use { rs ->
                    buildList { while (rs.next()) add((1..rs.metaData.columnCount).map { rs.getString(it) }) }
                }
            }
        }
        assumeTrue("recruit 표 없음 (export_dict.py를 다시 실행)", loaded != null)
        loaded!!
    }

    private fun ids(vararg zh: String) = r.tagIds(zh.toList())

    private fun lows(vararg zh: String) = r.combos(ids(*zh)).associate { c -> c.tags.map { r.ko(it) } to c.low }

    @Test fun seniorIsFiveStar() = assertEquals(5, lows("资深干员")[listOf("특별 채용")])

    @Test fun topSeniorIsSixStar() = assertEquals(6, lows("高级资深干员")[listOf("고급 특별 채용")])

    @Test fun controlAloneIsFourStar() {
        val low = lows("控场")[listOf("제어형")]
        assertNotNull(low)
        assertTrue(low!! >= 4)
    }

    @Test fun commonTagsNotGuaranteed() {
        val got = lows("输出", "近卫干员", "远程位")
        assertFalse(listOf("딜러") in got)
        assertFalse(listOf("가드") in got)
    }

    @Test fun robot() {
        assertTrue(r.robot(ids("支援机械", "治疗")))
        assertFalse(r.robot(ids("治疗")))
    }

    @Test fun describeHasRows() {
        val rows = r.describe(ids("控场", "输出", "近卫干员", "远程位", "新手"))
        assertTrue(rows.isNotEmpty() && rows[0].second >= 4)
    }
}
