package com.akkr.overlay

import android.Manifest
import android.app.Activity
import android.content.ComponentName
import android.content.Intent
import android.content.pm.PackageManager
import android.media.projection.MediaProjectionManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import kotlin.concurrent.thread

/**
 * 시작 화면.
 *  안드로이드 14 이상 (접근성 방식): 접근성에서 이 앱을 켜면, 게임을 켤 때마다 알아서 번역. 여기서는 켜고 끄기만.
 *  그 아래 (화면 녹화 방식): 다른 앱 위에 표시 허용 → 번역 시작 → 화면 녹화 허용('전체 화면').
 *  업데이트: 켤 때마다 사전·앱 새 판을 확인해 사전은 패치로 바로 맞추고, 새 앱은 '업데이트 확인'으로 설치 (Updater).
 */
class MainActivity : Activity() {

    companion object {
        /** 새 앱 알림을 눌러 열었을 때: 바로 업데이트 */
        const val EXTRA_UPDATE = "update"
    }

    private val a11yMode = Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE
    private lateinit var status: TextView
    private lateinit var start: Button
    private lateinit var toggle: Button
    private lateinit var versions: TextView
    private lateinit var updateBtn: Button
    private lateinit var bar: ProgressBar
    private lateinit var updStatus: TextView
    @Volatile private var dictReady = false
    private var entries: Int? = null
    private var dictVer: String? = null
    private var dictBuilt: String? = null
    @Volatile private var checking = false
    @Volatile private var pendingApk: Updater.Info? = null

    private val listener = Updater.Listener { text, pct ->
        runOnUiThread {
            updStatus.text = text
            bar.visibility = View.VISIBLE
            bar.isIndeterminate = pct < 0
            if (pct >= 0) bar.progress = pct
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val pad = (16 * resources.displayMetrics.density).toInt()
        status = TextView(this).apply { textSize = 16f; setPadding(0, 0, 0, pad) }
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.TOP
            setPadding(pad, pad * 2, pad, pad)
            addView(TextView(context).apply { text = getString(R.string.app_name); textSize = 22f; setPadding(0, 0, 0, pad) })
            addView(status)
        }
        versions = TextView(this).apply { textSize = 13f }
        updateBtn = Button(this).apply { text = "업데이트 확인"; setOnClickListener { onUpdateClick() } }
        bar = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply { max = 100; visibility = View.GONE }
        updStatus = TextView(this).apply { textSize = 13f; setPadding(0, 0, 0, pad) }
        root.addView(versions)
        root.addView(updateBtn)
        root.addView(bar)
        root.addView(updStatus)
        showVersions()
        Updater.addListener(listener)
        fun help(text: String) = TextView(this).apply { textSize = 14f; setPadding(0, pad, 0, 0); this.text = text }

        if (a11yMode) {
            root.addView(Button(this).apply {
                text = "1. 접근성에서 이 앱 켜기"
                setOnClickListener { startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) }
            })
            root.addView(Button(this).apply {
                text = "켜기가 막혀 있으면: 제한된 설정 허용"
                setOnClickListener {
                    startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName")))
                }
            })
            toggle = Button(this).apply { setOnClickListener { setOn(!isOn()); refresh() } }
            root.addView(toggle)
            root.addView(help(
                "접근성에서 '${getString(R.string.app_name)}'을 켜 두면, 명일방주를 켤 때마다 번역 상자가 뜹니다.\n" +
                    "켜기 버튼이 회색으로 막혀 있으면: 위의 '제한된 설정 허용' → 오른쪽 위 ⋮ → '제한된 설정 허용' 후 다시 켜세요.\n" +
                    "번역 상자는 불투명하지만 터치는 그대로 게임으로 넘어갑니다.",
            ))
        } else {
            root.addView(Button(this).apply { text = "1. 다른 앱 위에 표시 허용"; setOnClickListener { openOverlaySettings() } })
            start = Button(this).apply { text = "2. 번역 시작"; isEnabled = false; setOnClickListener { requestCapture() } }
            root.addView(start)
            root.addView(Button(this).apply {
                text = "번역 중지"
                setOnClickListener {
                    startService(Intent(this@MainActivity, OverlayService::class.java).setAction(OverlayService.ACTION_STOP))
                    refresh()
                }
            })
            root.addView(help(
                "화면 녹화를 허용할 때 '전체 화면'을 고르세요.\n" +
                    "번역 상자 아래 글자가 바뀌면 그 상자가 사라지고, 화면이 멈추면 새로 번역합니다.\n" +
                    "알림을 누르면 번역이 멈춥니다.",
            ))
            if (Build.VERSION.SDK_INT >= 33 &&
                checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
            ) requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 2)
        }
        setContentView(root)

        // 사전 준비 (처음 한 번: 사전을 받거나 APK 안의 것을 복사) → 업데이트 확인
        status.text = "사전 준비 중…"
        runCheck(manual = intent?.getBooleanExtra(EXTRA_UPDATE, false) == true)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (intent.getBooleanExtra(EXTRA_UPDATE, false)) runCheck(manual = true)
    }

    override fun onDestroy() {
        Updater.removeListener(listener)
        super.onDestroy()
    }

    /** 사전을 열어 문장 수·판 번호를 읽는다 (백그라운드 스레드). */
    private fun openDict(): Boolean {
        val r = runCatching {
            DictStore.open(this) { msg -> runOnUiThread { status.text = msg } }.let { d ->
                try { Triple(d.count(), d.meta("dict_version"), d.meta("built")) } finally { d.close() }
            }
        }
        runOnUiThread {
            dictReady = r.isSuccess
            r.onSuccess { (n, v, b) -> entries = n; dictVer = v; dictBuilt = b }
            r.onFailure { status.text = "사전을 준비하지 못했습니다: ${it.message}\n'업데이트 확인'을 눌러 다시 시도하세요." }
            bar.visibility = View.GONE
            showVersions()
            refresh()
        }
        return r.isSuccess
    }

    private fun showVersions() {
        if (!::versions.isInitialized) return
        val dict = when {
            dictVer != null -> "${dictBuilt.orEmpty()} (판 $dictVer)"
            dictReady -> "${dictBuilt.orEmpty()} (판 번호 없음)"
            else -> "없음"
        }
        versions.text = "앱 ${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE}) · 사전 $dict"
    }

    private fun onUpdateClick() {
        val apk = pendingApk
        if (apk == null) {
            runCheck(manual = true)
            return
        }
        if (checking) return
        checking = true
        updateBtn.isEnabled = false
        thread {
            try { installApk(apk) } finally { checking = false; runOnUiThread { updateBtn.isEnabled = true } }
        }
    }

    /** 사전을 준비하고 업데이트를 확인한다. manual(버튼·알림)이면 전체 사전도 받고, 새 앱이 있으면 바로 받아 설치 화면을 연다. */
    private fun runCheck(manual: Boolean) {
        if (checking) return
        checking = true
        updateBtn.isEnabled = false
        thread {
            try {
                if (!dictReady && !openDict()) return@thread
                val r = runCatching { Updater.check(this, manual) }
                val o = r.getOrNull()
                if (o != null && (o.kind == Updater.Kind.PATCHED || o.kind == Updater.Kind.FULL)) openDict()
                runOnUiThread {
                    bar.visibility = View.GONE
                    updStatus.text = r.fold({ describe(it) }, { "업데이트 확인 실패: ${it.message}" })
                }
                val apk = o?.info?.takeIf { it.apkNewer }
                pendingApk = apk
                if (apk != null) {
                    runOnUiThread { updateBtn.text = "앱 업데이트 (${apk.apkName}, ${size(apk.apk?.size ?: 0)})" }
                    if (manual) installApk(apk)
                }
            } finally {
                checking = false
                runOnUiThread { updateBtn.isEnabled = true }
            }
        }
    }

    private fun describe(o: Updater.Outcome): String {
        val s = when (o.kind) {
            Updater.Kind.NONE -> "최신 사전입니다."
            Updater.Kind.PATCHED -> "사전 패치 완료: 판 ${o.from} → ${o.to} (패치 ${size(o.bytes)}, ${"%.1f".format(o.millis / 1000.0)}초)"
            Updater.Kind.FULL -> "전체 사전을 받았습니다: 판 ${o.to} (${size(o.bytes)}, ${"%.1f".format(o.millis / 1000.0)}초)"
            Updater.Kind.NEED_FULL -> "새 사전(판 ${o.info.dictVersion})은 전체를 받아야 합니다 (${size(o.info.full.size)}). " +
                "와이파이에서 자동으로 받거나, '업데이트 확인'을 누르세요."
        }
        val note = if (o.note.isNotEmpty()) "\n(${o.note})" else ""
        val apk = if (o.info.apkNewer) "\n새 앱 ${o.info.apkName}이 있습니다." else ""
        return s + note + apk
    }

    /** 새 APK를 받아 설치 화면을 연다 (백그라운드 스레드). */
    private fun installApk(info: Updater.Info) {
        val r = runCatching { Updater.downloadApk(this, info) }
        runOnUiThread {
            bar.visibility = View.GONE
            r.onSuccess { f ->
                updStatus.text = if (Updater.install(this, f)) "설치 화면에서 '업데이트'를 누르세요."
                else "'이 출처의 앱 설치 허용'을 켠 뒤 '앱 업데이트'를 다시 누르세요."
            }
            r.onFailure { updStatus.text = "앱을 받지 못했습니다: ${it.message}" }
        }
    }

    private fun size(b: Long) = if (b < 1_000_000) "%.1fKB".format(b / 1000.0) else "%.0fMB".format(b / 1_000_000.0)

    override fun onResume() {
        super.onResume()
        refresh()
    }

    private fun isOn() = getSharedPreferences(ReaderService.PREFS, MODE_PRIVATE).getBoolean(ReaderService.KEY_ON, true)

    private fun setOn(on: Boolean) {
        getSharedPreferences(ReaderService.PREFS, MODE_PRIVATE).edit().putBoolean(ReaderService.KEY_ON, on).apply()
    }

    /** 접근성 설정에서 이 앱의 서비스가 켜져 있나 */
    private fun a11yEnabled(): Boolean {
        val me = ComponentName(this, ReaderService::class.java).flattenToString()
        val list = Settings.Secure.getString(contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES).orEmpty()
        return list.split(':').any { it.equals(me, ignoreCase = true) }
    }

    private fun refresh() {
        if (!::status.isInitialized || !dictReady) return
        if (a11yMode) {
            val a11y = a11yEnabled()
            status.text = buildString {
                append("사전: ${"%,d".format(entries ?: 0)}문장\n")
                append("접근성: ${if (a11y) "켜짐" else "꺼짐 (켜야 번역됨)"}\n")
                append("번역: ${if (isOn()) "켬" else "끔"}")
            }
            toggle.text = if (isOn()) "번역 끄기" else "번역 켜기"
            return
        }
        val overlayOk = Settings.canDrawOverlays(this)
        status.text = buildString {
            append("사전: ${"%,d".format(entries ?: 0)}문장\n")
            append("다른 앱 위에 표시: ${if (overlayOk) "허용됨" else "허용 필요"}\n")
            append("번역: ${if (OverlayService.running) "실행 중" else "꺼짐"}")
        }
        start.isEnabled = overlayOk && !OverlayService.running
    }

    private fun openOverlaySettings() {
        startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName")))
    }

    private fun requestCapture() {
        val mpm = getSystemService(MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        @Suppress("DEPRECATION")
        startActivityForResult(mpm.createScreenCaptureIntent(), 1)
    }

    @Deprecated("Activity 기본 API")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        @Suppress("DEPRECATION")
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != 1 || resultCode != RESULT_OK || data == null) return
        val intent = Intent(this, OverlayService::class.java)
            .putExtra(OverlayService.EXTRA_CODE, resultCode)
            .putExtra(OverlayService.EXTRA_DATA, data)
        startForegroundService(intent)
        status.postDelayed({ refresh() }, 800)
        // 게임으로 돌아가기 쉽게 홈 화면으로
        moveTaskToBack(true)
    }
}
