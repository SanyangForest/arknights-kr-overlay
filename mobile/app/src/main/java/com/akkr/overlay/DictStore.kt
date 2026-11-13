package com.akkr.overlay

import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import java.io.File
import java.io.IOException

/**
 * 사전(SQLite). 앱 저장소의 dict.db를 읽는다. 54만 문장을 메모리에 올리지 않고 필요한 것만 찾아 읽는다.
 *  사전을 뺀 APK(기본): 처음 실행할 때 GitHub에서 전체 사전을 받고, 그 뒤로는 Updater가 패치를 붙인다.
 *  사전을 넣은 APK(오프라인용, gradle -PbundleDict=true): assets/dict.db가 지금 것보다 새 판일 때만 꺼내 쓴다.
 * 파일은 PC에서 mobile/export_dict.py로 만들고 publish_update.py가 판 번호(meta.dict_version)를 찍어 올린다.
 */
/** 사전 검색. 기기에서는 DictStore(SQLite), PC 시험에서는 JDBC로 같은 파일을 읽는다. */
interface Dict {
    fun exact(key: String): String?
    fun firstWithPrefix(prefix: String): Pair<String, String>?
    fun skelExact(key: String): Pair<String, String>?
    fun skelFirstWithPrefix(prefix: String): Triple<String, String, String>?
    fun bucket(head: String, minLen: Int, maxLen: Int, limit: Int = 3000): List<Pair<String, String>>
    fun skelBucket(head: String, minLen: Int, maxLen: Int, limit: Int = 2000): List<Triple<String, String, String>>
    /** 끝이 tail로 끝나는 짧은 문장들 (24자 이하, 첫 글자를 틀리게 읽은 OCR의 후보). */
    fun suffixBucket(tail: String, minLen: Int, maxLen: Int, limit: Int = 2000): List<Pair<String, String>>
    fun alias(ocr: String): String?
    fun meta(k: String): String?
    /** 스킬 설명 틀(정규화한 원문)의 레벨별 수치: 한 줄에 한 레벨 '이름=값,…'. 없으면 null. */
    fun levels(key: String): String?
    /** 숫자만 다른 같은 문장이 여럿인 틀 (재능의 잠재·정예화 단계별 수치 등). */
    fun skelMulti(key: String): Boolean
    /** prefix로 시작하는 틀이 둘 이상인가 (앞부분만 보일 때 어느 틀인지 모름). */
    fun skelPrefixAmbiguous(prefix: String): Boolean
    /** prefix로 시작하는 문장이 둘 이상인가. */
    fun prefixAmbiguous(prefix: String): Boolean
}

/**
 * 안드로이드 rawQuery는 ? 인자를 모두 글자로 넘긴다. 'length(key) BETWEEN ? AND ?'에 글자가 들어가면
 * 숫자 < 글자로 비교되어 늘 거짓이므로, 길이·개수 같은 정수는 SQL에 직접 넣는다.
 */
class DictStore private constructor(private var conn: SQLiteDatabase, private var gen: Int) : Dict {

    companion object {
        private const val NAME = "dict.db"
        private const val TAG = "AkkrOverlay"
        /** 쓰는 동안 이만큼마다 하루 한 번 업데이트 확인이 됐는지 봄 (접근성 서비스는 며칠씩 켜져 있으므로). */
        private const val PROBE_MS = 10 * 60_000L

        /** 사전 파일을 바꿔 끼울 때마다 오름. 열려 있는 DictStore는 다음 검색 때 새 파일을 다시 연다. */
        @Volatile private var generation = 0
        @Volatile private var appContext: Context? = null

        fun file(ctx: Context) = File(ctx.filesDir, NAME)

        /** 사전을 준비해 연다 (처음엔 복사·내려받기로 몇 초~몇십 초). 시작 화면과 접근성 서비스가 동시에 불러도 한 번만 준비. */
        @Synchronized
        fun open(context: Context, progress: (String) -> Unit = {}): DictStore {
            val app = context.applicationContext
            appContext = app
            prepare(app, progress)
            return DictStore(openRead(file(app)), generation)
        }

        /**
         * 사전 파일이 있게 한다. 받은 패치가 붙은 사전은 앱을 업데이트해도 덮어쓰지 않는다
         * (assets의 사전이 더 새 판일 때만 바꿈). 없으면 전체 사전을 받는다 (메인 스레드에서는 못 함).
         */
        @Synchronized
        fun prepare(context: Context, progress: (String) -> Unit = {}) {
            val app = context.applicationContext
            val file = file(app)
            File(app.filesDir, "$NAME.version").delete() // 0.33까지 쓰던 복사 표시
            val stamp = File(app.filesDir, "$NAME.apk")
            val apkTime = app.packageManager.getPackageInfo(app.packageName, 0).lastUpdateTime.toString()
            if (!stamp.exists() || stamp.readText() != apkTime) {
                if (app.assets.list("")?.contains(NAME) == true) {
                    progress("사전 준비 중… (처음 한 번)")
                    val tmp = File(app.filesDir, "$NAME.tmp")
                    app.assets.open(NAME).use { input -> tmp.outputStream().use { input.copyTo(it, 1 shl 20) } }
                    val bundled = versionOf(tmp)
                    if (!file.exists() || bundled > installedVersion(app)) {
                        install(app, tmp)
                        Log.i(TAG, "APK 안의 사전(판 $bundled)을 꺼냄")
                    } else {
                        tmp.delete()
                    }
                }
                stamp.writeText(apkTime)
            }
            if (!file.exists()) {
                check(Looper.myLooper() != Looper.getMainLooper()) { "사전을 받는 중에는 메인 스레드에서 열 수 없음" }
                progress("사전 정보 받는 중…")
                val info = try {
                    Updater.fetchInfo()
                } catch (e: IOException) {
                    throw IOException("사전을 받으려면 인터넷 연결이 필요합니다 (${e.message})", e)
                }
                progress("사전 받는 중… (${info.full.size / 1_000_000}MB, 처음 한 번)")
                Updater.downloadFull(app, info)
            }
        }

        /** 새 사전 파일(같은 폴더)을 dict.db 자리에 바꿔 끼운다. 열려 있던 연결은 옛 파일을 끝까지 읽을 수 있다. */
        @Synchronized
        fun install(context: Context, newFile: File) {
            if (!newFile.renameTo(file(context))) throw IOException("사전 파일을 바꾸지 못함")
            generation++
        }

        /** 깔린 사전의 판 번호 (yyyyMMddHHmm). 없거나 판 번호가 없는 옛 사전이면 0. */
        fun installedVersion(context: Context): Long = versionOf(file(context))

        fun versionOf(f: File): Long {
            if (!f.exists()) return 0
            return try {
                openRead(f).use { db ->
                    db.rawQuery("SELECT v FROM meta WHERE k='dict_version'", null).use { if (it.moveToFirst()) it.getString(0).toLongOrNull() ?: 0 else 0 }
                }
            } catch (e: Exception) {
                Log.w(TAG, "사전 판을 읽지 못함: $f", e)
                0
            }
        }

        private fun openRead(f: File): SQLiteDatabase =
            SQLiteDatabase.openDatabase(f.path, null, SQLiteDatabase.OPEN_READONLY or SQLiteDatabase.NO_LOCALIZED_COLLATORS)

        const val ALIAS_SQL = "SELECT zh FROM aliases WHERE ocr=? AND (screen='*' OR zh NOT LIKE '=%') ORDER BY screen='*' DESC LIMIT 1"

        // 문자열 범위 검색의 끝 (이 글자로 시작하는 키 전부): 'abc' ≤ key < 'abc￿'
        private fun upper(prefix: String) = prefix + '\uffff'
    }

    private var lastProbe = SystemClock.elapsedRealtime() - PROBE_MS + 60_000L

    /** 지금 쓸 연결. 업데이트로 파일이 바뀌었으면 다시 열고, 가끔 하루 한 번 업데이트 확인을 부른다. */
    private val db: SQLiteDatabase
        get() {
            if (gen != generation) reopen()
            val now = SystemClock.elapsedRealtime()
            if (now - lastProbe > PROBE_MS) {
                lastProbe = now
                appContext?.let { Updater.autoCheck(it) }
            }
            return conn
        }

    @Synchronized
    private fun reopen() {
        val g = generation
        val ctx = appContext ?: return
        if (g == gen) return
        val old = conn
        conn = openRead(file(ctx))
        gen = g
        old.close()
        Log.i(TAG, "업데이트된 사전을 다시 엶 (판 ${meta("dict_version")})")
    }

    fun count(): Int = meta("entries")?.toInt() ?: 0

    /** 공개모집 조합 계산 데이터 (recruit_* 표가 없는 옛 사전이면 null). */
    fun recruit(): Recruit? = Recruit.load { sql ->
        db.rawQuery(sql, null).use { c -> buildList { while (c.moveToNext()) add((0 until c.columnCount).map { c.getString(it) }) } }
    }

    override fun meta(k: String): String? = db.rawQuery("SELECT v FROM meta WHERE k=?", arrayOf(k)).use {
        if (it.moveToFirst()) it.getString(0) else null
    }

    override fun suffixBucket(tail: String, minLen: Int, maxLen: Int, limit: Int): List<Pair<String, String>> {
        val r = tail.reversed()
        return db.rawQuery(
            "SELECT r.key, e.ko FROM rev r JOIN entries e ON e.key = r.key " +
                "WHERE r.rkey>=? AND r.rkey<? AND length(r.key) BETWEEN $minLen AND $maxLen LIMIT $limit",
            arrayOf(r, upper(r)),
        ).use { c -> buildList { while (c.moveToNext()) add(c.getString(0) to c.getString(1)) } }
    }

    override fun exact(key: String): String? = db.rawQuery("SELECT ko FROM entries WHERE key=?", arrayOf(key)).use {
        if (it.moveToFirst()) it.getString(0) else null
    }

    /** prefix로 시작하는 키 중 사전 순 첫 번째 (PC판 bisect와 같음). */
    override fun firstWithPrefix(prefix: String): Pair<String, String>? =
        db.rawQuery(
            "SELECT key, ko FROM entries WHERE key>=? AND key<? ORDER BY key LIMIT 1", arrayOf(prefix, upper(prefix)),
        ).use { if (it.moveToFirst()) it.getString(0) to it.getString(1) else null }

    override fun skelExact(key: String): Pair<String, String>? =
        db.rawQuery("SELECT zh, ko FROM skel WHERE key=?", arrayOf(key)).use {
            if (it.moveToFirst()) it.getString(0) to it.getString(1) else null
        }

    override fun skelFirstWithPrefix(prefix: String): Triple<String, String, String>? =
        db.rawQuery(
            "SELECT key, zh, ko FROM skel WHERE key>=? AND key<? ORDER BY key LIMIT 1", arrayOf(prefix, upper(prefix)),
        ).use { if (it.moveToFirst()) Triple(it.getString(0), it.getString(1), it.getString(2)) else null }

    /** head로 시작하고 길이가 [minLen, maxLen]인 키들 (비슷한 문장 후보). */
    override fun bucket(head: String, minLen: Int, maxLen: Int, limit: Int): List<Pair<String, String>> =
        db.rawQuery(
            "SELECT key, ko FROM entries WHERE key>=? AND key<? AND length(key) BETWEEN $minLen AND $maxLen LIMIT $limit",
            arrayOf(head, upper(head)),
        ).use { c -> buildList { while (c.moveToNext()) add(c.getString(0) to c.getString(1)) } }

    override fun skelBucket(head: String, minLen: Int, maxLen: Int, limit: Int): List<Triple<String, String, String>> =
        db.rawQuery(
            "SELECT key, zh, ko FROM skel WHERE key>=? AND key<? AND length(key) BETWEEN $minLen AND $maxLen LIMIT $limit",
            arrayOf(head, upper(head)),
        ).use { c -> buildList { while (c.moveToNext()) add(Triple(c.getString(0), c.getString(1), c.getString(2))) } }

    /**
     * OCR 오독 별칭. 폰은 화면을 가리지 않으므로 모든 화면의 별칭을 쓰되, 그 화면에서만 다르게 옮기는
     * 바로 번역('=')은 공통('*')일 때만 쓴다 (설정 화면의 关闭 = 끄기가 다른 화면에 퍼지지 않게).
     */
    override fun alias(ocr: String): String? =
        db.rawQuery(ALIAS_SQL, arrayOf(ocr)).use {
            if (it.moveToFirst()) it.getString(0) else null
        }

    override fun levels(key: String): String? = db.rawQuery("SELECT sets FROM levels WHERE key=?", arrayOf(key)).use {
        if (it.moveToFirst()) it.getString(0) else null
    }

    override fun skelMulti(key: String): Boolean =
        db.rawQuery("SELECT 1 FROM skel_multi WHERE key=?", arrayOf(key)).use { it.moveToFirst() }

    override fun skelPrefixAmbiguous(prefix: String): Boolean =
        db.rawQuery("SELECT count(*) FROM (SELECT 1 FROM skel WHERE key>=? AND key<? LIMIT 2)", arrayOf(prefix, upper(prefix))).use {
            it.moveToFirst() && it.getInt(0) >= 2
        }

    override fun prefixAmbiguous(prefix: String): Boolean =
        db.rawQuery("SELECT count(*) FROM (SELECT 1 FROM entries WHERE key>=? AND key<? LIMIT 2)", arrayOf(prefix, upper(prefix))).use {
            it.moveToFirst() && it.getInt(0) >= 2
        }

    fun close() = conn.close()
}
