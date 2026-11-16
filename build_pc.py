"""PC 오버레이 배포판 만들기: 파이썬 없이 압축만 풀어 쓰는 폴더 + zip (친구에게 공유용).

  python build_pc.py            → dist/명방한글오버레이/ 와 dist/명방한글오버레이-pc-<날짜>.zip
  python build_pc.py --no-zip   폴더만

PyInstaller로 overlay/overlay_app.pyw를 exe로 묶고(.venv에 pyinstaller 필요), 실행에 필요한 데이터만 옆에 복사한다.
폴더 구성은 overlay/paths.py 설명과 같다 (data/pc.db, models/, user/, _internal/app/).
data/pc.db는 마지막으로 올린 판(tmp/published/pc-<판>.db, update.json "pc")이 있으면 그것을 넣어 받은 사람이
자동 업데이트로 패치만 받게 하고, 없으면 out/에서 새로 묶는다 (그러면 첫 업데이트 때 전체를 받음).

넣지 않는 것 (개인 정보·라이선스):
  - overlay/settings.json(박사 이름), 로그, captures/, overlay/data/(보유 오퍼레이터·창고·낱말장)
  - MAA 아이템 템플릿(AGPL): 창고 인식은 꺼진 채로 나감
  - 학습용 도구·녹화 화면 (tools/ocrtrain)
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OV = ROOT / "overlay"
DIST = ROOT / "dist"
NAME = "명방한글오버레이"
EXE = "ak-kr-overlay"
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
CODE_FILES = ["screens.json", "ocr_aliases.tsv", "marquee.tsv", "planner_template.html", "templates"]
PUB = ROOT / "tmp" / "published"
FONT = Path("C:/Windows/Fonts/NotoSansKR-VF.ttf")
RAPID = ROOT / ".venv" / "Lib" / "site-packages" / "rapidocr_onnxruntime"
RAPID_MODULES = [f"{pkg}.{mod}" for pkg, mods in (("ch_ppocr_v3_det", ("text_detect", "utils")),
                                                  ("ch_ppocr_v3_rec", ("text_recognize", "utils")),
                                                  ("ch_ppocr_v2_cls", ("text_cls", "utils"))) for mod in mods]
SETTINGS = {"_설명": "nickname: 게임 속 박사 이름(트레이 메뉴 '박사 이름 설정'). render_style: natural(한국어판처럼) / box(상자로 덮기). "
                     "auto_update: 사전 자동 업데이트",
            "nickname": "", "show_in_capture": False, "render_style": "natural", "auto_update": True}

README = """명일방주 한국어 오버레이 (중국 서버 PC판용)
==========================================

중국 서버(bilibili) 명일방주 PC판 화면의 중국어를 읽어 그 자리에 한국어를 덮어 그립니다.
번역은 한국 서버의 공식 문장을 씁니다 (한국 서버에 아직 없는 이벤트는 직접 번역).
게임 파일·메모리는 건드리지 않고, 게임에 입력도 보내지 않습니다 (화면만 읽음).

쓰는 법
  1. 압축을 아무 폴더에나 풉니다 (한글·띄어쓰기 없는 경로 권장, 예: D:\\ak-kr-overlay).
  2. ak-kr-overlay.exe 를 실행하면 작업 표시줄 오른쪽 트레이에 '한' 아이콘이 생깁니다.
  3. 게임을 '창 모드'로 켜면 저절로 번역이 시작됩니다.
     - F9: 번역 표시 켜기/끄기    F10: 끄기
     - 트레이 아이콘 오른쪽 클릭: 설정 메뉴
  4. 트레이 메뉴 '박사 이름 설정'에 게임 속 박사 이름을 넣으면 대사 속 이름도 맞춰 번역합니다.
  5. 사전은 켤 때와 6시간마다 저절로 업데이트를 확인합니다 (바뀐 부분만, 보통 수십 KB).
     받으면 알림이 뜨고, 트레이 메뉴 '다시 시작해서 사전 업데이트 적용' 또는 다음 실행 때 적용됩니다.
     새 프로그램 버전이 나오면 알려 주기만 합니다 (트레이 메뉴 '새 버전 받기').

트레이 메뉴
  - 한국어판처럼 그리기: 켜면 중국어 글자를 지우고 그 자리에 한국어를 씁니다. 끄면 예전처럼 상자로 덮습니다.
  - 방송·화면 공유에 보이기: OBS·디스코드 화면 공유에 번역이 보이게 합니다 (관리자 권한으로 다시 켜짐).
  - 육성 계산기 열기: 오퍼레이터 목록·창고 화면을 넘겨 보면 기록된 내용으로 육성 재료를 계산합니다.

기능
  - 스토리 따라가기: 대사가 한 글자씩 나오는 중에도 그 대사 전체의 공식 번역을 바로 보여 줍니다.
  - 공개모집 확정 조합, 스테이지 드롭 정보(펭귄 물류 통계), 보유 오퍼레이터 표시.

요구 사항
  - Windows 10/11 64비트, DirectX 12 지원 그래픽카드 권장 (없으면 CPU로 느리게 동작)
  - 게임이 관리자 권한으로 돌면 '방송·화면 공유에 보이기'를 쓸 때 관리자 권한 확인 창이 한 번 뜹니다.

문제가 생기면 user 폴더의 overlay_app_log.txt 를 보내 주세요.
글꼴: Noto Sans KR (SIL Open Font License 1.1, https://openfontlicense.org) — _internal/app/fonts
"""


def run(cmd, **kw):
    print("$", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def pyinstaller(work):
    seps = []
    for name in CODE_FILES:
        src = OV / name
        seps += ["--add-data", f"{src};app/{name}" if src.is_dir() else f"{src};app"]
    if FONT.exists():
        seps += ["--add-data", f"{FONT};app/fonts"]
    run([VENV_PY, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--onedir",
         "--name", EXE, "--icon", OV / "app_icon.ico", "--paths", OV,
         "--distpath", work / "dist", "--workpath", work / "build", "--specpath", work,
         "--collect-data", "rapidocr_onnxruntime", "--collect-binaries", "onnxruntime",
         # RapidOCR은 자기 폴더를 sys.path에 넣고 'ch_ppocr_v3_det' 같은 이름으로 불러온다: 그 이름 그대로 넣음
         "--paths", RAPID, *[x for m in RAPID_MODULES for x in ("--hidden-import", m)],
         "--hidden-import", "pystray._win32",
         # 학습·점검용 무거운 패키지가 딸려 들어가지 않게
         "--exclude-module", "paddle", "--exclude-module", "torch", "--exclude-module", "matplotlib",
         *seps, OV / "overlay_app.pyw"], cwd=ROOT)
    return work / "dist" / EXE


VC_DLLS = ["msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll"]


def _version(path):
    import win32api
    try:
        info = win32api.GetFileVersionInfo(str(path), "\\")
        ms, ls = info["FileVersionMS"] & 0xFFFFFFFF, info["FileVersionLS"] & 0xFFFFFFFF
        return (ms >> 16, ms & 0xFFFF, ls >> 16)
    except Exception:  # noqa: BLE001
        return (0, 0, 0)


def newer_vc_runtime(internal):
    """PyInstaller가 파이썬 설치본의 옛 Visual C++ 런타임(14.28)을 넣는데, onnxruntime 1.24는 14.4 이상이 필요해
    'DLL 초기화 루틴을 실행할 수 없습니다'로 못 뜬다. 이 컴퓨터의 더 새 런타임(재배포 가능 파일)으로 바꾼다."""
    sys32 = Path("C:/Windows/System32")
    for name in VC_DLLS:
        src, dst = sys32 / name, internal / name
        if src.exists() and (not dst.exists() or _version(src) > _version(dst)):
            shutil.copy2(src, dst)
            print(f"Visual C++ 런타임 {name} → {'.'.join(map(str, _version(src)))}")


def pc_db():
    """넣을 사전 묶음: 마지막으로 올린 판이 있으면 그것, 없으면 out/에서 새로 (build_pcdb.py)."""
    upd = json.loads((ROOT / "update.json").read_text(encoding="utf-8")) if (ROOT / "update.json").exists() else {}
    ver = (upd.get("pc") or {}).get("version")
    published = PUB / f"pc-{ver}.db" if ver else None
    if published and published.exists():
        print(f"사전 묶음: 올린 판 {ver}")
        return published
    import build_pcdb
    out = build_pcdb.build(ROOT / "out" / "pc.db")
    print(f"사전 묶음: 올린 판이 없어 새로 묶음 ({out}) — 받은 사람은 첫 업데이트 때 전체를 받음")
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-zip", action="store_true")
    ap.add_argument("--pcdb", type=Path, help="넣을 사전 묶음 (publish_update.py가 이번에 올리는 판을 넘김)")
    a = ap.parse_args()
    work = ROOT / "tmp" / "build_pc"
    built = pyinstaller(work)
    target = DIST / NAME
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(built, target)
    newer_vc_runtime(target / "_internal")
    data = target / "data"
    data.mkdir()
    shutil.copy2(a.pcdb or pc_db(), data / "pc.db")
    models = target / "models"
    models.mkdir()
    cur = json.loads((ROOT / "tools" / "ocr_models" / "current.json").read_text(encoding="utf-8"))
    shutil.copytree(ROOT / "tools" / "ocr_models" / cur["rec"], models / cur["rec"])
    (models / "current.json").write_text(json.dumps({"rec": cur["rec"]}, ensure_ascii=False, indent=1), encoding="utf-8")
    user = target / "user"
    user.mkdir()
    (user / "settings.json").write_text(json.dumps(SETTINGS, ensure_ascii=False, indent=1), encoding="utf-8")
    (target / "읽어보기.txt").write_text(README, encoding="utf-8-sig")
    size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file()) / 1e6
    print(f"폴더: {target} ({size:.0f}MB)")
    if not a.no_zip:
        z = shutil.make_archive(str(DIST / f"{NAME}-pc-{time.strftime('%Y%m%d')}"), "zip", DIST, NAME)
        print(f"압축: {z} ({Path(z).stat().st_size / 1e6:.0f}MB)")


if __name__ == "__main__":
    main()
