# 명방 한글 오버레이

명일방주 중국 서버(빌리빌리판)의 중국어를 읽어(OCR), 그 위에 한국어 번역 상자를 띄우는 개인용 도구입니다.
공개 게임 데이터(중국 서버·한국 서버)에서 같은 자리의 문장끼리 짝지은 사전에, 한국 서버에 아직 없는 문장의 번역을 더해 씁니다.

- **PC판** (`overlay/`): 파이썬. PC 클라이언트 화면을 읽어 번역 상자를 띄웁니다.
- **휴대폰판** (`mobile/`): 안드로이드 앱(Kotlin, ML Kit 중국어 OCR). 안드로이드 14 이상은 접근성 서비스로 게임 창만 캡처합니다.

## 휴대폰 앱 설치·업데이트

1. [릴리스](https://github.com/SanyangForest/arknights-kr-overlay/releases)에서 가장 최근의 `akkr-overlay-<버전>.apk`를 받아 설치합니다.
2. 앱을 처음 켜면 사전(약 65MB)을 한 번 받습니다.
3. 그 뒤로는 앱이 켤 때와 하루에 한 번 `update.json`을 확인해
   - 사전이 바뀌었으면 바뀐 문장만 담은 **패치**(보통 몇 KB~몇 MB)를 받아 바로 붙입니다.
     패치가 망가졌거나 너무 오래 업데이트하지 않았으면 전체 사전을 다시 받습니다 (데이터 요금이 드는 망에서는 '업데이트 확인'을 눌렀을 때만).
   - 새 앱이 나왔으면 '업데이트 확인'을 눌러 받고, 설치 화면에서 '업데이트'를 누르면 됩니다.
4. 접근성에서 이 앱을 켜 두면 명일방주를 켤 때마다 번역 상자가 뜹니다 (앱 화면의 안내 참고).

## 사전 만들기 (PC)

```
python build_dict.py data\gamedata          # 게임 데이터 → out\dict_zh_ko.json
finalize.bat                                 # 번역 반영 → 이름 통일 → 사전 → 점검
```

- `build_dict.py`: 중국 서버·한국 서버 데이터를 짝지어 사전을 만듭니다. 한국 서버에 없는 문장은 `out/untranslated.tsv`로.
- `extra/*.tsv`: 직접 번역·커뮤니티 표기·덮어쓰기. `merge_translations.py`, `unify_names.py`가 반영합니다.
- 게임 데이터(`data/`), 중간 결과(`out/`, `chunks/` 등)는 저장소에 넣지 않습니다.
- `build_story.py data\gamedata`: 스토리 대본 색인 `out/story.db` (스토리 따라가기용, finalize.bat이 함께 만듦).
- `overlay/vocab.py build`: 녹화 화면으로 화면별 낱말장 `out/screen_vocab.json` (일부만 읽힌 글 알아보기).

## PC 오버레이

- 스토리 따라가기(`overlay/story.py`): 대사 한두 줄로 어느 스토리의 몇 번째 대사인지 찾고, 그다음부터는 대본 순서대로
  공식 한국어를 씁니다. 대사가 한 글자씩 나오는 중에도 대사 전체의 번역이 바로 뜹니다.
- 그리는 방식(`render.STYLE`, 트레이 '한국어판처럼 그리기'): `natural`은 원문 글자만 지우고 그 자리에 한국어를 씀, `box`는 상자로 덮음.
- 녹화 화면으로 점검: `cd overlay` → `python replay.py captures\session1 --from 212 --to 229` → `captures\session1_replay\`.
- 파일 위치는 `overlay/paths.py` 한곳에서 정합니다 (개발 / 배포판).

## PC 배포판 만들기 (공유용)

```
python build_pc.py      # → dist\명방한글오버레이\ (폴더) + dist\명방한글오버레이-pc-<날짜>.zip
```

- PyInstaller로 exe를 만들고 사전 묶음(`data/pc.db`, `build_pcdb.py`)·OCR 모델을 옆에 복사합니다. 박사 이름·기록·MAA 템플릿(AGPL)은 넣지 않습니다.
- 자동 업데이트(`overlay/updater.py`): 켤 때와 6시간마다 update.json의 `"pc"`를 보고 바뀐 줄만 담은 패치를 받아
  `data/pc.db.new`로 두고 다시 켤 때 적용합니다. `publish_update.py`가 휴대폰 사전과 함께 PC 패치를 올립니다
  (`--no-pc`로 빼기, `--pc-app`으로 배포판 zip도 올려 `"pc_app"` 새 버전 알림). 버전은 `updater.APP_VERSION`.
  점검: `ak-kr-overlay.exe --update-check` → `user` 폴더의 `update_check.txt`.
- 받은 사람은 압축을 풀고 `ak-kr-overlay.exe` 실행. 점검: `ak-kr-overlay.exe --selftest 그림.png` → `user\selftest.txt`.

## 휴대폰 업데이트 올리기 (PC)

```
python publish_update.py
```

1. `.venv` 파이썬으로 `mobile/export_dict.py`를 돌려 휴대폰용 사전(SQLite)을 만들고
2. 판 번호(`yyyyMMddHHmm`)를 찍은 사본을 `tmp/published/`에 두고, 지난번에 올린 사전과 비교한 패치를 만듭니다 (`mobile/make_patch.py`, 패치를 적용해 새 사전과 같은지까지 확인).
3. `gh release create`로 패치·전체 사전(그리고 `versionCode`가 바뀌었으면 새 APK)을 올리고
4. `update.json`을 고쳐 커밋·푸시합니다. 앱은 `raw.githubusercontent.com`의 이 파일을 봅니다.

`--no-export`(이미 만든 사전 그대로), `--dry-run`(올리지 않고 파일만), `--notes "..."`(릴리스 설명) 옵션이 있습니다.
`tmp/published/dict-<판>.db`(지난번에 올린 사전)를 지우면 다음에는 패치 없이 전체 사전만 올라갑니다.

### update.json

```json
{
  "dict": {
    "version": "202611140100",
    "full": {"url": "…/dict-202611140100.db.gz", "sha256": "…", "size": 65170223, "db_size": 164990976},
    "patches": [{"from": "202611130000", "to": "202611140100", "url": "…", "sha256": "…", "size": 1108}],
    "min_patch_from": "202611130000"
  },
  "apk": {"versionCode": 34, "versionName": "0.34", "url": "…/akkr-overlay-0.34.apk", "sha256": "…", "size": 16338722}
}
```

## 앱 빌드

`mobile/`에서 (JDK 21, 안드로이드 SDK):

```
gradlew testDebugUnitTest assembleRelease
```

- 기본 APK에는 사전이 들어 있지 않습니다 (약 16MB, 첫 실행 때 받음). 인터넷 없이 쓸 APK는 `-PbundleDict=true`.
- 업데이트 주소를 바꿔 시험하려면 `-PupdateUrl=http://127.0.0.1:8765/update.json` (`adb reverse tcp:8765 tcp:8765`로 PC의 시험 서버에 연결).
- `testDebugUnitTest`는 휴대폰 번역 로직이 PC판과 같은 결과를 내는지 녹화해 둔 OCR 줄로 비교합니다.

## 알림

명일방주의 게임 텍스트와 이미지의 권리는 Hypergryph / Yostar에 있습니다. 이 저장소는 비공식 개인 도구입니다.
