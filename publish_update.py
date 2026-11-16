"""휴대폰 앱 업데이트 올리기 (GitHub 릴리스 + update.json).

사전을 다시 만들고(mobile/export_dict.py), 지난번에 올린 사전과 비교한 패치를 만들어(mobile/make_patch.py)
GitHub 릴리스로 올린 뒤, 앱이 확인하는 update.json을 고쳐 커밋·푸시한다.
앱은 build.gradle.kts의 versionCode가 update.json의 것과 다를 때만 빌드해서 함께 올린다.

  python publish_update.py                 사전 다시 만들기 → 패치 → 릴리스 → update.json 푸시
  python publish_update.py --no-export     이미 만든 사전(mobile/app/src/main/assets/dict.db)을 그대로 올림
  python publish_update.py --dry-run       릴리스·푸시 없이 tmp/published/에 파일만 만들어 봄
  python publish_update.py --notes "..."   릴리스 설명에 덧붙일 말
  python publish_update.py --apk 경로       앱을 빌드하지 않고 이 APK를 올림 (versionCode가 바뀐 경우)
  python publish_update.py --no-pc         PC 배포판 사전 묶음(pc.db)은 빼고
  python publish_update.py --pc-app        PC 배포판 zip도 새로 만들어 올림 (overlay/updater.py APP_VERSION이 바뀌었을 때)

PC 배포판(build_pc.py)의 자동 업데이트도 함께: out/의 사전·대본·게임 표를 pc.db로 묶어(build_pcdb.py) 지난번 것과의
패치를 올리고 update.json "pc"에 적는다. 전체 pc.db.gz(약 100MB)는 처음이거나 전체를 올린 뒤 패치가 PC_FULL_EVERY개
쌓였을 때만 올린다 (받는 쪽은 전체 판에서부터 패치를 잇는다).

지난번에 올린 사전은 tmp/published/dict-<판>.db로 남겨 둔다. 이것이 없으면 패치를 못 만들어 전체 사전만 올리고,
앱은 다음 확인 때 전체 사전(약 65MB)을 받는다.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MOBILE = ROOT / "mobile"
sys.path.insert(0, str(MOBILE))

import make_patch  # noqa: E402

REPO = "SanyangForest/arknights-kr-overlay"
ASSET = MOBILE / "app" / "src" / "main" / "assets" / "dict.db"
PUB = ROOT / "tmp" / "published"
UPDATE_JSON = ROOT / "update.json"
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
GRADLE_FILE = MOBILE / "app" / "build.gradle.kts"
APK_OUT = MOBILE / "app" / "build" / "outputs" / "apk" / "release" / "app-release.apk"
SDK = Path(os.environ.get("ANDROID_HOME", r"D:\Android\Sdk"))
GRADLE_ENV = {"JAVA_HOME": r"D:\Android\jdk-21", "ANDROID_HOME": str(SDK), "GRADLE_USER_HOME": r"D:\Android\.gradle"}
KEEP_PATCHES = 30  # update.json에 남길 패치 수 (이보다 오래된 사전은 전체를 받음)
PC_FULL_EVERY = 10  # PC 전체 묶음은 패치가 이만큼 쌓이면 다시 올림 (KEEP_PATCHES보다 작아야 전체 판부터 패치가 이어짐)


def run(cmd, **kw):
    print("$", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def asset_url(tag: str, name: str) -> str:
    return f"https://github.com/{REPO}/releases/download/{tag}/{name}"


def asset_entry(tag: str, path: Path, **extra) -> dict:
    return {"url": asset_url(tag, path.name), "sha256": sha256(path), "size": path.stat().st_size, **extra}


def gradle_version() -> tuple[int, str]:
    s = GRADLE_FILE.read_text(encoding="utf-8")
    return int(re.search(r"versionCode\s*=\s*(\d+)", s)[1]), re.search(r'versionName\s*=\s*"([^"]+)"', s)[1]


def apk_version(apk: Path) -> int | None:
    """aapt2로 APK의 versionCode를 읽는다 (빌드 도구가 없으면 None)."""
    tools = sorted((SDK / "build-tools").glob("*/aapt2.exe"))
    if not tools:
        return None
    out = subprocess.run([str(tools[-1]), "dump", "badging", str(apk)], capture_output=True, text=True, encoding="utf-8").stdout
    m = re.search(r"versionCode='(\d+)'", out)
    return int(m[1]) if m else None


def build_apk() -> Path:
    # 기본 업데이트 주소(raw.githubusercontent.com)와 사전 뺀 APK로 빌드 (-PupdateUrl, -PbundleDict 없이)
    run([MOBILE / "gradlew.bat", "assembleRelease"], cwd=MOBILE, env={**os.environ, **GRADLE_ENV})
    return APK_OUT


def table_meta(db: Path) -> dict:
    import sqlite3
    c = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        return dict(c.execute("SELECT k, v FROM meta WHERE k IN ('built', 'entries')"))
    finally:
        c.close()


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-export", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--notes", default="")
    ap.add_argument("--apk", type=Path)
    ap.add_argument("--no-pc", action="store_true")
    ap.add_argument("--pc-app", action="store_true")
    a = ap.parse_args()

    prev = json.loads(UPDATE_JSON.read_text(encoding="utf-8")) if UPDATE_JSON.exists() else {}
    prev_dict, prev_apk = prev.get("dict") or {}, prev.get("apk") or {}
    prev_pc, prev_pc_app = prev.get("pc") or {}, prev.get("pc_app") or {}

    # 1) 사전 만들기 (.venv 파이썬이어야 함: 시스템 파이썬은 조용히 옛 사전을 남김)
    if not a.no_export:
        before = ASSET.stat().st_mtime if ASSET.exists() else 0
        run([VENV_PY, MOBILE / "export_dict.py", ASSET])
        if not ASSET.exists() or ASSET.stat().st_mtime <= before:
            sys.exit("사전이 새로 만들어지지 않았습니다 (export_dict.py 출력 확인)")

    # 2) 이번 판 번호 (yyyyMMddHHmm, 지난 판보다 커야 함) → 올릴 사본에 찍기
    ver = time.strftime("%Y%m%d%H%M")
    if prev_dict.get("version") and int(ver) <= int(prev_dict["version"]):
        ver = str(int(prev_dict["version"]) + 1)
    PUB.mkdir(parents=True, exist_ok=True)
    new_db = PUB / f"dict-{ver}.db"
    shutil.copyfile(ASSET, new_db)
    make_patch.stamp(new_db, ver)

    # 3) 지난번에 올린 사전과의 패치 + 전체 사전 (패치는 적용해 보고 같은지 확인까지)
    prev_ver = prev_dict.get("version")
    old_db = PUB / f"dict-{prev_ver}.db" if prev_ver else None
    if old_db and not old_db.exists():
        print(f"경고: 지난번 사전 {old_db}가 없어 패치를 못 만듭니다. 전체 사전만 올립니다.")
        old_db = None
    t0 = time.time()
    res = make_patch.build(old_db, new_db, PUB)
    print(f"패치·압축 {time.time() - t0:.0f}초")
    stats = res["stats"]
    changes = {t: v for t, v in (stats or {}).items() if t != "content_changed" and v != (0, 0)}
    dict_changed = stats is None or stats["content_changed"]

    # 3-2) PC 배포판 사전 묶음: pc.db 새로 → 지난번 것과의 패치
    pc_changed, pc_res, new_pc = False, None, None
    if not a.no_pc:
        import build_pcdb
        new_pc = PUB / f"pc-{ver}.db"
        build_pcdb.build(new_pc, ver)
        prev_pc_ver = prev_pc.get("version")
        old_pc = PUB / f"pc-{prev_pc_ver}.db" if prev_pc_ver else None
        if old_pc and not old_pc.exists():
            print(f"경고: 지난번 PC 묶음 {old_pc}가 없어 패치를 못 만듭니다. 전체만 올립니다.")
            old_pc = None
        pc_res = make_patch.build(old_pc, new_pc, PUB, prefix="pc")
        pc_changed = pc_res["stats"] is None or pc_res["stats"]["content_changed"]
        if not pc_changed:
            new_pc.unlink(missing_ok=True)

    # 3-3) PC 배포판 zip (요청했을 때만)
    pc_zip, pc_app_version = None, None
    if a.pc_app:
        sys.path.insert(0, str(ROOT / "overlay"))
        import updater
        run([VENV_PY, ROOT / "build_pc.py"], cwd=ROOT)
        pc_zip = max((ROOT / "dist").glob("*-pc-*.zip"), key=lambda f: f.stat().st_mtime)
        pc_app_version = updater.APP_VERSION

    # 4) 앱: versionCode가 바뀌었을 때만
    code, name = gradle_version()
    apk_changed = code != prev_apk.get("versionCode")
    if apk_changed and prev_apk.get("versionCode") and code < prev_apk["versionCode"]:
        sys.exit(f"versionCode {code}가 이미 올린 {prev_apk['versionCode']}보다 작습니다")

    if not dict_changed and not apk_changed and not pc_changed and not pc_zip:
        print("사전도 앱도 바뀐 것이 없어 올리지 않습니다.")
        for f in (new_db, res["full"]):
            f.unlink(missing_ok=True)
        if res["patch"]:
            res["patch"].unlink(missing_ok=True)
        return

    files = []
    upload_dict = dict_changed
    if upload_dict:
        files.append(res["full"])
        if res["patch"]:
            files.append(res["patch"])
    pc_patches, pc_full = [], prev_pc.get("full")
    if pc_changed:
        pc_patches = list(prev_pc.get("patches", [])) if pc_res["patch"] else []
        if pc_patches and pc_patches[-1]["to"] != prev_pc.get("version"):
            pc_patches = []
        if pc_res["patch"]:
            pc_patches.append({"from": prev_pc["version"], "to": ver, "file": pc_res["patch"]})
        since_full = [x for x in pc_patches if pc_full and int(x["to"]) > int(pc_full.get("version", "0"))]
        if not pc_full or not pc_res["patch"] or len(since_full) >= PC_FULL_EVERY:
            files.append(pc_res["full"])
            pc_full = None  # 아래에서 이번 판 전체로
        else:
            pc_res["full"].unlink(missing_ok=True)
        if pc_res["patch"]:
            files.append(pc_res["patch"])
    if pc_zip:
        files.append(pc_zip)
    apk_pub = None
    if apk_changed:
        apk = a.apk or (APK_OUT if a.dry_run and APK_OUT.exists() else None) or build_apk()
        got = apk_version(apk)
        if got is not None and got != code:
            sys.exit(f"{apk}의 versionCode가 {got} (build.gradle.kts는 {code})")
        apk_pub = PUB / f"akkr-overlay-{name}.apk"
        shutil.copyfile(apk, apk_pub)
        files.append(apk_pub)

    tag = f"d{ver}" if (upload_dict or pc_changed) else (f"app-{name}" if apk_changed else f"pc-{pc_app_version}")
    meta = table_meta(new_db)
    title = " · ".join(filter(None, [f"사전 {meta.get('built', ver)}" if (upload_dict or pc_changed) else "",
                                     f"앱 {name}" if apk_changed else "", f"PC {pc_app_version}" if pc_zip else ""]))
    lines = []
    if upload_dict:
        lines.append(f"사전 판 {ver} (문장 {int(meta.get('entries', 0)):,}개)")
        if res["patch"]:
            lines.append(f"패치 {prev_ver} → {ver}: " + ", ".join(f"{t} -{d}/+{n}" for t, (d, n) in changes.items()))
    if pc_changed:
        lines.append(f"PC 사전 묶음 판 {ver}" + (f" (패치 {pc_res['patch'].stat().st_size:,}바이트)" if pc_res["patch"] else " (전체)"))
    if apk_changed:
        lines.append(f"앱 {name} (versionCode {code})")
    if pc_zip:
        lines.append(f"PC 배포판 {pc_app_version}: {pc_zip.name} (압축을 풀고 ak-kr-overlay.exe 실행)")
    if a.notes:
        lines += ["", a.notes]
    notes = "\n".join(lines)
    for f in files:
        print(f"  {f.name}: {f.stat().st_size:,} 바이트")
    print(f"릴리스 {tag}: {title}\n{notes}")

    # 5) update.json (패치는 이어지는 것만, 최근 KEEP_PATCHES개)
    upd = {"format": 1, "repo": REPO}
    if upload_dict:
        patches = list(prev_dict.get("patches", [])) if res["patch"] else []
        if patches and patches[-1]["to"] != prev_ver:
            patches = []
        if res["patch"]:
            patches.append({"from": prev_ver, "to": ver, **asset_entry(tag, res["patch"])})
        patches = patches[-KEEP_PATCHES:]
        upd["dict"] = {
            "version": ver, "built": meta.get("built", ""), "entries": int(meta.get("entries", 0)),
            "full": asset_entry(tag, res["full"], db_size=new_db.stat().st_size),
            "min_patch_from": patches[0]["from"] if patches else ver,
            "patches": patches,
        }
    else:
        upd["dict"] = prev_dict
    upd["apk"] = ({"versionCode": code, "versionName": name, **asset_entry(tag, apk_pub)} if apk_changed else prev_apk)
    if pc_changed:
        pmeta = table_meta(new_pc)
        entries = [x if "file" not in x else {"from": x["from"], "to": x["to"], **asset_entry(tag, x["file"])}
                   for x in pc_patches][-KEEP_PATCHES:]
        upd["pc"] = {
            "version": ver, "built": pmeta.get("built", ""), "entries": int(pmeta.get("entries", 0)),
            "full": pc_full or asset_entry(tag, pc_res["full"], version=ver, db_size=new_pc.stat().st_size),
            "min_patch_from": entries[0]["from"] if entries else ver,
            "patches": entries,
        }
    elif prev_pc:
        upd["pc"] = prev_pc
    if pc_zip:
        upd["pc_app"] = {"version": pc_app_version, **asset_entry(tag, pc_zip),
                         "page": f"https://github.com/{REPO}/releases/tag/{tag}"}
    elif prev_pc_app:
        upd["pc_app"] = prev_pc_app

    if a.dry_run:
        out = PUB / "update.dry-run.json"
        out.write_text(json.dumps(upd, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"(시험) 릴리스·푸시 안 함. update.json 예정 내용: {out}")
        return

    run(["gh", "release", "create", tag, *files, "--repo", REPO, "--title", title, "--notes", notes])
    UPDATE_JSON.write_text(json.dumps(upd, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    run(["git", "add", "update.json"], cwd=ROOT)
    run(["git", "commit", "-m", f"업데이트: {title}", "--", "update.json"], cwd=ROOT)
    try:
        run(["git", "push"], cwd=ROOT)
    except subprocess.CalledProcessError:
        print("푸시 실패: 릴리스는 올라갔습니다. 인터넷을 확인하고 'git push'만 다시 하세요.")
        raise

    # 6) 정리: 다음 패치에 쓸 이번 사전과 그 앞 사전만 남김. 사전 넣은 오프라인 빌드도 이번 판 번호를 갖게 assets에도 찍음
    if upload_dict:
        make_patch.stamp(ASSET, ver)
        keep = {new_db.name, f"dict-{prev_ver}.db"}
        for f in PUB.glob("dict-*.db"):
            if f.name not in keep:
                f.unlink()
    if pc_changed:
        keep = {new_pc.name, f"pc-{prev_pc.get('version')}.db"}
        for f in PUB.glob("pc-*.db"):
            if f.name not in keep:
                f.unlink()
    for f in list(PUB.glob("*.gz")) + list(PUB.glob("*.apk")):
        if f not in files:
            f.unlink()
    print(f"완료: https://github.com/{REPO}/releases/tag/{tag}")


if __name__ == "__main__":
    main()
