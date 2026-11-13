package com.akkr.overlay

import android.Manifest
import android.app.Activity
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.database.sqlite.SQLiteDatabase
import android.net.ConnectivityManager
import android.net.Uri
import android.os.Build
import android.os.SystemClock
import android.provider.Settings
import android.util.Log
import androidx.core.content.FileProvider
import org.json.JSONObject
import java.io.File
import java.io.FilterInputStream
import java.io.IOException
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.util.concurrent.CopyOnWriteArraySet
import java.util.concurrent.locks.ReentrantLock
import java.util.zip.GZIPInputStream
import kotlin.concurrent.thread

/**
 * 업데이트 (MAA처럼): GitHub 저장소의 update.json을 앱을 켤 때와 하루에 한 번 확인한다.
 *  사전: 바뀐 줄만 담은 패치(dict-patch-<이전판>-<새판>.db.gz, PC의 mobile/make_patch.py가 만듦)를 받아
 *        사전 사본에 붙이고(ATTACH → 한 트랜잭션에서 DELETE/INSERT) 바꿔 끼운다.
 *        패치가 없거나 너무 크거나 실패하면 전체 사전(dict-<판>.db.gz)을 받는다.
 *  앱: 새 APK를 받아 설치 화면을 연다 (설치는 사용자가 누름).
 * 올리는 쪽은 PC의 publish_update.py.
 */
object Updater {
    private const val TAG = "AkkrUpdate"
    private const val PREFS = "updater"
    private const val KEY_LAST_CHECK = "last_check"
    private const val KEY_NOTIFIED_APK = "notified_apk"
    private const val DAY = 24 * 3600 * 1000L
    /** 이어 붙일 패치의 합이 전체 사전(압축)의 이만큼을 넘으면 전체를 받는다. */
    private const val PATCH_RATIO = 0.5
    private const val CHANNEL = "update"

    class Asset(val url: String, val sha256: String, val size: Long)
    class Patch(val from: Long, val to: Long, val asset: Asset)
    class Info(
        val dictVersion: Long, val dictBuilt: String, val full: Asset, val dbSize: Long,
        val patches: List<Patch>, val minPatchFrom: Long,
        val apkCode: Int, val apkName: String, val apk: Asset?,
    ) {
        val apkNewer get() = apk != null && apkCode > BuildConfig.VERSION_CODE
    }

    enum class Kind { NONE, PATCHED, FULL, NEED_FULL }

    /** 확인 결과. kind=NEED_FULL은 자동 확인에서 데이터 요금이 드는 망이라 전체 사전을 미룬 경우. */
    class Outcome(val kind: Kind, val info: Info, val from: Long, val to: Long, val bytes: Long, val millis: Long, val note: String = "")

    // ---- 진행 상황: 시작 화면이 붙어서 보여 줌 (패치는 접근성 서비스 쪽에서 돌 수도 있음) ----
    fun interface Listener { fun onProgress(text: String, percent: Int) }
    private val listeners = CopyOnWriteArraySet<Listener>()
    fun addListener(l: Listener) = listeners.add(l)
    fun removeListener(l: Listener) = listeners.remove(l)
    private var lastPct = -2
    private var lastText = ""
    /** percent: 0~100, -1은 얼마나 남았는지 모름. 같은 값은 다시 알리지 않음. */
    internal fun report(text: String, percent: Int = -1) {
        if (percent == lastPct && text == lastText) return
        lastPct = percent; lastText = text
        listeners.forEach { it.onProgress(text, percent) }
    }

    private val busy = ReentrantLock()
    val running get() = busy.isLocked

    fun fetchInfo(): Info {
        // raw.githubusercontent.com은 몇 분 캐시하므로 쿼리를 붙여 새로 받음
        val sep = if ('?' in BuildConfig.UPDATE_URL) '&' else '?'
        val text = connect("${BuildConfig.UPDATE_URL}${sep}t=${System.currentTimeMillis() / 60000}").let { c ->
            try { c.inputStream.use { it.readBytes().decodeToString() } } finally { c.disconnect() }
        }
        val j = JSONObject(text)
        val d = j.getJSONObject("dict")
        fun asset(o: JSONObject) = Asset(o.getString("url"), o.getString("sha256").lowercase(), o.getLong("size"))
        val full = d.getJSONObject("full")
        val ps = d.optJSONArray("patches")
        val patches = (0 until (ps?.length() ?: 0)).map { i ->
            val p = ps!!.getJSONObject(i)
            Patch(p.getString("from").toLong(), p.getString("to").toLong(), asset(p))
        }
        val apk = j.optJSONObject("apk")
        return Info(
            d.getString("version").toLong(), d.optString("built"), asset(full), full.optLong("db_size"),
            patches, d.optString("min_patch_from", "0").ifEmpty { "0" }.toLong(),
            apk?.optInt("versionCode") ?: 0, apk?.optString("versionName").orEmpty(),
            apk?.takeIf { it.has("url") }?.let(::asset),
        )
    }

    /**
     * 사전을 최신으로 맞춘다. manual=false(앱 시작·하루 한 번)면 전체 사전은 와이파이처럼 요금 없는 망에서만 받는다.
     * 다른 확인이 돌고 있으면 그것이 끝날 때까지 기다린다. 새 APK가 있는지는 결과의 info.apkNewer.
     */
    fun check(ctx: Context, manual: Boolean): Outcome {
        val app = ctx.applicationContext
        busy.lock()
        try {
            report("업데이트 확인 중…")
            val info = fetchInfo()
            prefs(app).edit().putLong(KEY_LAST_CHECK, System.currentTimeMillis()).apply()
            if (!DictStore.file(app).exists()) {
                // 처음 실행 (사전을 아직 못 받음): 시작 화면이 연 DictStore.open과 같은 길로 받음
                val t0 = SystemClock.elapsedRealtime()
                DictStore.prepare(app) { report(it) }
                return Outcome(Kind.FULL, info, 0, info.dictVersion, info.full.size, SystemClock.elapsedRealtime() - t0)
            }
            val cur = DictStore.installedVersion(app)
            if (cur >= info.dictVersion) return Outcome(Kind.NONE, info, cur, cur, 0, 0)
            val t0 = SystemClock.elapsedRealtime()
            val chain = chain(info, cur)
            if (chain != null) {
                try {
                    applyPatches(app, chain)
                    return Outcome(Kind.PATCHED, info, cur, info.dictVersion, chain.sumOf { it.asset.size }, SystemClock.elapsedRealtime() - t0)
                } catch (e: Exception) {
                    Log.w(TAG, "패치 실패, 전체 사전으로", e)
                    report("패치 실패 (${e.message}), 전체 사전을 받습니다")
                    if (!manual && metered(app)) {
                        return Outcome(Kind.NEED_FULL, info, cur, cur, 0, 0, "패치 실패: ${e.message}")
                    }
                    val t1 = SystemClock.elapsedRealtime()
                    downloadFull(app, info)
                    return Outcome(Kind.FULL, info, cur, info.dictVersion, info.full.size, SystemClock.elapsedRealtime() - t1,
                        "패치 실패: ${e.message}")
                }
            }
            if (!manual && metered(app)) return Outcome(Kind.NEED_FULL, info, cur, cur, 0, 0)
            downloadFull(app, info)
            return Outcome(Kind.FULL, info, cur, info.dictVersion, info.full.size, SystemClock.elapsedRealtime() - t0)
        } finally {
            busy.unlock()
        }
    }

    /** 하루에 한 번 (접근성 서비스가 사전을 쓰는 동안 DictStore가 부름). 새 APK는 알림으로만 알림. */
    fun autoCheck(ctx: Context) {
        val app = ctx.applicationContext
        if (System.currentTimeMillis() - prefs(app).getLong(KEY_LAST_CHECK, 0) < DAY || busy.isLocked) return
        // 실패해도 한 시간 동안 다시 시도하지 않게 먼저 적어 둠 (성공하면 check가 다시 적음)
        prefs(app).edit().putLong(KEY_LAST_CHECK, System.currentTimeMillis() - DAY + 3600_000L).apply()
        thread(name = "akkr-update", isDaemon = true) {
            try {
                val o = check(app, manual = false)
                Log.i(TAG, "자동 확인: ${o.kind} ${o.from} → ${o.to}")
                if (o.info.apkNewer) notifyApk(app, o.info)
            } catch (e: Exception) {
                Log.w(TAG, "자동 확인 실패", e)
            }
        }
    }

    /** 지금 사전(cur)에서 최신 판까지 이어지는 패치들. 없거나 전체보다 크게 손해면 null (전체를 받음). */
    fun chain(info: Info, cur: Long): List<Patch>? {
        if (cur <= 0 || cur < info.minPatchFrom) return null
        val byFrom = info.patches.associateBy { it.from }
        val out = mutableListOf<Patch>()
        var v = cur
        while (v != info.dictVersion) {
            val p = byFrom[v] ?: return null
            if (p.to <= v || out.size > 200) return null
            out += p
            v = p.to
        }
        return out.takeIf { it.sumOf { p -> p.asset.size } <= info.full.size * PATCH_RATIO }
    }

    // ---- 사전 패치 ----

    private fun applyPatches(ctx: Context, chain: List<Patch>) {
        val dir = File(ctx.cacheDir, "update").apply { mkdirs() }
        val t0 = SystemClock.elapsedRealtime()
        val work = File(ctx.filesDir, "dict.db.patching")
        val files = mutableListOf<File>()
        try {
            chain.forEachIndexed { i, p ->
                val f = File(dir, "patch-$i.db")
                files += f
                download(p.asset, "사전 패치 받는 중 (${i + 1}/${chain.size})") { gunzip(it, f) }
            }
            val t1 = SystemClock.elapsedRealtime()
            report("사전 복사 중…")
            DictStore.file(ctx).copyTo(work, overwrite = true)
            val t2 = SystemClock.elapsedRealtime()
            report("패치 적용 중…")
            val db = SQLiteDatabase.openDatabase(work.path, null, SQLiteDatabase.OPEN_READWRITE or SQLiteDatabase.NO_LOCALIZED_COLLATORS)
            try {
                db.disableWriteAheadLogging() // ATTACH는 WAL에서 못 씀, 결과 파일은 읽기 전용으로 열 것이므로 일반 저널로
                chain.forEachIndexed { i, p -> applyPatch(db, files[i], p) }
            } finally {
                db.close()
            }
            DictStore.install(ctx, work)
            val t3 = SystemClock.elapsedRealtime()
            Log.i(TAG, "패치 ${chain.size}개 적용: 받기 ${t1 - t0}ms, 복사 ${t2 - t1}ms, 적용·교체 ${t3 - t2}ms")
        } finally {
            work.delete()
            for (s in listOf("-journal", "-wal", "-shm")) File(work.path + s).delete()
            files.forEach { it.delete() }
        }
    }

    private fun q(name: String) = "\"" + name.replace("\"", "\"\"") + "\""

    private fun strings(db: SQLiteDatabase, sql: String, col: Int = 0): List<String?> =
        db.rawQuery(sql, null).use { c -> buildList { while (c.moveToNext()) add(c.getString(col)) } }

    /** make_patch.py의 apply_patch와 같은 순서·같은 확인. 실패하면 이 패치의 변경은 모두 되돌린다. */
    private fun applyPatch(db: SQLiteDatabase, file: File, p: Patch) {
        db.execSQL("ATTACH DATABASE ? AS p", arrayOf<Any>(file.path))
        try {
            val info = db.rawQuery("SELECT k, v FROM p.patch_info", null).use { c ->
                buildMap { while (c.moveToNext()) put(c.getString(0), c.getString(1)) }
            }
            if (info["format"] != "1") throw IOException("모르는 패치 형식 ${info["format"]}")
            val have = strings(db, "SELECT v FROM main.meta WHERE k='dict_version'").firstOrNull()
            if (info["from"] != p.from.toString() || have != info["from"]) throw IOException("패치 시작 판(${info["from"]})이 사전 판($have)과 다름")
            val names = strings(db, "SELECT name FROM p.sqlite_master WHERE type='table'").filterNotNull().toSet()
            db.beginTransaction()
            try {
                db.rawQuery("SELECT name, sql FROM p.schema", null).use { c ->
                    while (c.moveToNext()) {
                        db.execSQL("DROP TABLE IF EXISTS main.${q(c.getString(0))}")
                        if (!c.isNull(1)) db.execSQL(c.getString(1))
                    }
                }
                val targets = names.filter { it.startsWith("del_") || it.startsWith("add_") }.map { it.substring(4) }.toSortedSet()
                for (t in targets) {
                    if ("del_$t" in names) {
                        val keys = strings(db, "PRAGMA p.table_info(${q("del_$t")})", 1).filterNotNull()
                        val cols = keys.joinToString(", ") { q(it) }
                        val lhs = if (keys.size == 1) cols else "($cols)"
                        db.execSQL("DELETE FROM main.${q(t)} WHERE $lhs IN (SELECT $cols FROM p.${q("del_$t")})")
                    }
                    if ("add_$t" in names) {
                        val cols = strings(db, "PRAGMA p.table_info(${q("add_$t")})", 1).filterNotNull().joinToString(", ") { q(it) }
                        db.execSQL("INSERT OR REPLACE INTO main.${q(t)} ($cols) SELECT $cols FROM p.${q("add_$t")}")
                    }
                }
                for ((k, v) in info) {
                    if (!k.startsWith("rows:")) continue
                    val n = strings(db, "SELECT count(*) FROM main.${q(k.substring(5))}").first()
                    if (n != v) throw IOException("${k.substring(5)} 줄 수 $n (기대 $v)")
                }
                val got = strings(db, "SELECT v FROM main.meta WHERE k='dict_version'").firstOrNull()
                if (got != p.to.toString()) throw IOException("패치 뒤 판 $got (기대 ${p.to})")
                db.setTransactionSuccessful()
            } finally {
                db.endTransaction()
            }
        } finally {
            db.execSQL("DETACH DATABASE p")
        }
    }

    // ---- 전체 사전 ----

    /** 전체 사전을 받아(압축을 풀며 바로 파일로) 바꿔 끼운다. DictStore.prepare(처음 실행)와 check가 부름. */
    fun downloadFull(ctx: Context, info: Info) {
        val app = ctx.applicationContext
        val need = (if (info.dbSize > 0) info.dbSize else info.full.size * 3) + (32 shl 20)
        if (app.filesDir.usableSpace < need) {
            throw IOException("저장 공간 부족: ${need shr 20}MB 필요, ${app.filesDir.usableSpace shr 20}MB 남음")
        }
        val tmp = File(app.filesDir, "dict.db.download")
        val t0 = SystemClock.elapsedRealtime()
        try {
            download(info.full, "사전 받는 중 (${info.full.size / 1_000_000}MB)") { gunzip(it, tmp) }
            Log.i(TAG, "전체 사전 받음: ${info.full.size} 바이트 → ${tmp.length()} 바이트, ${SystemClock.elapsedRealtime() - t0}ms")
            val v = DictStore.versionOf(tmp)
            if (v != info.dictVersion) throw IOException("받은 사전의 판($v)이 update.json(${info.dictVersion})과 다름")
            DictStore.install(app, tmp)
        } finally {
            tmp.delete()
        }
    }

    // ---- 앱 (APK) ----

    /** 새 APK를 받는다 (이미 받아 둔 것이 맞으면 그대로). 받은 파일의 패키지·버전도 확인. */
    fun downloadApk(ctx: Context, info: Info): File {
        val a = info.apk ?: throw IOException("update.json에 APK가 없음")
        val dir = File(ctx.cacheDir, "update").apply { mkdirs() }
        dir.listFiles { f -> f.name.endsWith(".apk") && f.name != "app-${info.apkCode}.apk" }?.forEach { it.delete() }
        val f = File(dir, "app-${info.apkCode}.apk")
        if (!(f.length() == a.size && sha256(f) == a.sha256)) {
            try {
                download(a, "앱 ${info.apkName} 받는 중 (${a.size / 1_000_000}MB)") { input -> f.outputStream().use { input.copyTo(it, 1 shl 16) } }
            } catch (e: Exception) {
                f.delete()
                throw e
            }
        }
        val pi = ctx.packageManager.getPackageArchiveInfo(f.path, 0) ?: run { f.delete(); throw IOException("받은 APK를 읽지 못함") }
        @Suppress("DEPRECATION")
        val code = if (Build.VERSION.SDK_INT >= 28) pi.longVersionCode else pi.versionCode.toLong()
        if (pi.packageName != ctx.packageName || code != info.apkCode.toLong()) {
            f.delete()
            throw IOException("받은 APK가 다름 (${pi.packageName} $code)")
        }
        Log.i(TAG, "APK ${info.apkName} 준비됨: ${f.length()} 바이트")
        report("앱 ${info.apkName} 받음", 100)
        return f
    }

    /** 설치 화면을 연다. '출처를 알 수 없는 앱' 허용이 아직이면 그 설정을 열고 false (허용 뒤 다시 누르면 됨). */
    fun install(activity: Activity, apk: File): Boolean {
        if (!activity.packageManager.canRequestPackageInstalls()) {
            activity.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${activity.packageName}")))
            return false
        }
        val uri = FileProvider.getUriForFile(activity, "${activity.packageName}.files", apk)
        activity.startActivity(
            Intent(Intent.ACTION_VIEW).setDataAndType(uri, "application/vnd.android.package-archive")
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK),
        )
        return true
    }

    private fun notifyApk(ctx: Context, info: Info) {
        val p = prefs(ctx)
        if (p.getInt(KEY_NOTIFIED_APK, 0) >= info.apkCode) return
        if (Build.VERSION.SDK_INT >= 33 &&
            ctx.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) return
        val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "업데이트", NotificationManager.IMPORTANCE_DEFAULT))
        val open = PendingIntent.getActivity(
            ctx, 3, Intent(ctx, MainActivity::class.java).putExtra(MainActivity.EXTRA_UPDATE, true).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        nm.notify(3, Notification.Builder(ctx, CHANNEL)
            .setSmallIcon(android.R.drawable.stat_sys_download_done)
            .setContentTitle("${ctx.getString(R.string.app_name)} ${info.apkName}")
            .setContentText("새 버전이 있습니다. 눌러서 업데이트")
            .setContentIntent(open).setAutoCancel(true).build())
        p.edit().putInt(KEY_NOTIFIED_APK, info.apkCode).apply()
    }

    // ---- 내려받기 ----

    private fun prefs(ctx: Context) = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    private fun metered(ctx: Context): Boolean =
        (ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager).isActiveNetworkMetered

    private fun connect(url: String): HttpURLConnection {
        val c = URL(url).openConnection() as HttpURLConnection
        c.connectTimeout = 15_000
        c.readTimeout = 30_000
        c.instanceFollowRedirects = true // GitHub 릴리스 파일은 다른 주소로 넘겨줌 (https → https)
        c.setRequestProperty("User-Agent", "akkr-overlay/${BuildConfig.VERSION_NAME}")
        val code = c.responseCode
        if (code !in 200..299) {
            c.disconnect()
            throw IOException("HTTP $code ($url)")
        }
        return c
    }

    /**
     * 받으면서 sha256을 재고, 받은 바이트 흐름을 sink에 넘긴다 (압축 풀기·저장). 끝까지 읽은 뒤 sha256이 다르면 예외.
     * sink가 흐름을 끝까지 안 읽어도 남은 바이트를 마저 읽어 해시에 넣는다.
     */
    private fun download(a: Asset, label: String, sink: (InputStream) -> Unit) {
        val c = connect(a.url)
        try {
            val total = if (a.size > 0) a.size else c.contentLengthLong
            val md = MessageDigest.getInstance("SHA-256")
            report(label, 0)
            c.inputStream.use { raw ->
                val counted = object : FilterInputStream(raw) {
                    var done = 0L
                    private fun add(buf: ByteArray, off: Int, n: Int) {
                        if (n <= 0) return
                        md.update(buf, off, n)
                        done += n
                        if (total > 0) report(label, (done * 100 / total).toInt().coerceIn(0, 100))
                    }
                    override fun read(): Int {
                        val b = super.read()
                        if (b >= 0) add(byteArrayOf(b.toByte()), 0, 1)
                        return b
                    }
                    override fun read(b: ByteArray, off: Int, len: Int): Int = super.read(b, off, len).also { add(b, off, it) }
                    override fun skip(n: Long): Long { // 건너뛰지 않고 읽어서 해시에 넣음
                        val buf = ByteArray(minOf(n, 8192L).toInt())
                        return read(buf, 0, buf.size).toLong().coerceAtLeast(0)
                    }
                }
                // sink가 닫아도(GZIPInputStream.use) 남은 바이트를 마저 읽을 수 있게 닫기는 무시
                try {
                    sink(object : FilterInputStream(counted) { override fun close() {} })
                } catch (e: java.util.zip.ZipException) {
                    throw IOException("받은 파일이 손상됨 (압축 오류: ${e.message})", e)
                }
                val buf = ByteArray(1 shl 16)
                while (counted.read(buf) >= 0) { /* 남은 바이트도 해시에 */ }
                if (total > 0 && counted.done != total) throw IOException("$label: ${counted.done}/$total 바이트만 받음")
            }
            val got = md.digest().joinToString("") { "%02x".format(it) }
            if (got != a.sha256) throw IOException("받은 파일이 손상됨 (sha256 불일치)")
        } finally {
            c.disconnect()
        }
    }

    private fun gunzip(input: InputStream, out: File) {
        GZIPInputStream(input, 1 shl 16).use { g -> out.outputStream().use { g.copyTo(it, 1 shl 20) } }
    }

    private fun sha256(f: File): String {
        val md = MessageDigest.getInstance("SHA-256")
        f.inputStream().use { s ->
            val buf = ByteArray(1 shl 16)
            while (true) {
                val n = s.read(buf)
                if (n < 0) break
                md.update(buf, 0, n)
            }
        }
        return md.digest().joinToString("") { "%02x".format(it) }
    }
}
