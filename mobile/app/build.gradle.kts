plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// 업데이트 정보 주소 (시험할 때 -PupdateUrl=http://127.0.0.1:8765/update.json 처럼 바꿈, adb reverse로 PC 서버에 연결)
val updateUrl = (findProperty("updateUrl") as String?)
    ?: "https://raw.githubusercontent.com/SanyangForest/arknights-kr-overlay/main/update.json"
// 사전(assets/dict.db, 165MB)을 APK에 넣을지: 기본은 빼고 첫 실행 때 받음. 인터넷 없이 쓸 APK는 -PbundleDict=true
val bundleDict = (findProperty("bundleDict") as String?) == "true"

android {
    namespace = "com.akkr.overlay"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.akkr.overlay"
        minSdk = 26
        targetSdk = 35
        versionCode = 38
        versionName = "0.38"
        // 요즘 안드로이드 폰(64비트 ARM)용 OCR 라이브러리만 넣어 설치 파일을 줄임
        ndk { abiFilters += "arm64-v8a" }
        buildConfigField("String", "UPDATE_URL", "\"$updateUrl\"")
        // 시험용 http 주소일 때만 암호화 안 된 연결 허용
        manifestPlaceholders["cleartext"] = updateUrl.startsWith("http:").toString()
    }
    buildFeatures { buildConfig = true }
    if (!bundleDict) {
        // aapt 기본 무시 목록 + dict.db
        androidResources.ignoreAssetsPattern = "!dict.db:!.svn:!.git:!.ds_store:!*.scc:.*:<dir>_*:!CVS:!thumbs.db:!picasa.ini:!*~"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            // 개인용: 디버그 키로 서명해 바로 설치할 수 있게
            signingConfig = signingConfigs.getByName("debug")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    // 기기 안에서 도는 중국어 글자 인식 (모델을 앱에 포함)
    implementation("com.google.mlkit:text-recognition-chinese:16.0.1")
    // 새 APK를 설치 화면에 넘기는 FileProvider (ML Kit이 이미 끌어오는 판)
    implementation("androidx.core:core:1.9.0")

    // PC에서 번역 로직을 PC판과 비교하는 시험 (app/src/test)
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.xerial:sqlite-jdbc:3.46.1.3")
}

android.testOptions.unitTests.all { it.testLogging { events("standard_out") } }
