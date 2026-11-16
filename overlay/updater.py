"""PC 배포판 자동 업데이트: GitHub의 update.json을 보고 사전 묶음(data/pc.db)을 패치로 고친다.

- update.json의 "pc" = 휴대폰 "dict"와 같은 모양 (version, full{version,url,sha256,size}, patches[{from,to,url,sha256,size}]).
  지금 판에서 이어지는 패치가 있으면 패치만(보통 수십 KB), 없으면 전체(약 95MB)를 받은 뒤 그 판부터 패치를 잇는다.
- 받은 결과는 data/pc.db.new로 두고, 다음에 앱을 켤 때 pc.db를 열기 전에 바꾼다 (apply_pending).
  쓰는 중인 파일은 Windows에서 바꿀 수 없고, 바꾸다 끊겨도 원래 사전이 남게.
- "pc_app"(새 앱 버전)은 받지 않고 알려만 준다 (트레이 메뉴에서 받는 곳을 연다).
게임·계정과는 상관없이 이 프로젝트의 GitHub 저장소에서 파일만 받는다.
"""

import hashlib
import json
import os
import shutil
import tempfile
import urllib.request
from pathlib import Path

import datastore
import dbpatch
import paths

APP_VERSION = "0.1"
UPDATE_URL = os.environ.get("AKKR_UPDATE_URL",
                            "https://raw.githubusercontent.com/SanyangForest/arknights-kr-overlay/main/update.json")
PENDING = datastore.PCDB.with_name("pc.db.new")
USER_AGENT = "akkr-overlay-pc"


def _open(url, timeout=30):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=timeout)


def fetch_manifest():
    with _open(UPDATE_URL, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def download(entry, dst, log):
    """entry{url, sha256, size}를 dst로 받고 sha256을 확인한다."""
    h = hashlib.sha256()
    got = 0
    with _open(entry["url"], timeout=60) as r, open(dst, "wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
            h.update(b)
            got += len(b)
            if entry.get("size", 0) > 20_000_000 and got % (20 << 20) < (1 << 20):
                log(f"받는 중 {got / 1e6:.0f}/{entry['size'] / 1e6:.0f}MB")
    if h.hexdigest() != entry["sha256"]:
        raise ValueError(f"받은 파일이 다름 (sha256): {entry['url']}")


def chain_from(version, patches):
    """version에서 시작해 이어지는 패치들 (끝까지 못 이으면 None)."""
    by_from = {p["from"]: p for p in patches}
    out, cur = [], version
    while cur in by_from:
        p = by_from[cur]
        out.append(p)
        cur = p["to"]
    return out, cur


def app_newer(manifest):
    """새 앱 버전이 있으면 그 정보 (없으면 None)."""
    app = manifest.get("pc_app") or {}
    try:
        newer = tuple(map(int, app.get("version", "0").split("."))) > tuple(map(int, APP_VERSION.split(".")))
    except ValueError:
        newer = False
    return app if newer else None


def check(log=print):
    """업데이트를 확인하고 사전이 새로 있으면 받아 둔다. 돌려주는 값: {"dict": 받은 판 또는 None, "app": 새 앱 정보 또는 None}."""
    manifest = fetch_manifest()
    result = {"dict": None, "app": app_newer(manifest), "current": None}
    pc = manifest.get("pc") or {}
    target = pc.get("version")
    base = PENDING if PENDING.exists() else datastore.PCDB
    cur = datastore.version(base)
    result["current"] = cur
    if not target or cur == target:
        return result
    work = Path(tempfile.mkdtemp(prefix="akkr_update_", dir=str(paths.USER)))
    try:
        db = work / "pc.db"
        patches = pc.get("patches", [])
        chain, end = chain_from(cur, patches) if cur else ([], None)
        if not chain or end != target:
            full = pc["full"]
            log(f"사전 전체 받기 (판 {full.get('version', target)}, {full['size'] / 1e6:.0f}MB)")
            download(full, work / "full.gz", log)
            dbpatch.gunzip(work / "full.gz", db)
            (work / "full.gz").unlink()
            chain, end = chain_from(dbpatch.dict_version(db), patches)
        else:
            log(f"사전 패치 {len(chain)}개 받기 ({cur} → {target})")
            shutil.copyfile(base, db)
        for i, p in enumerate(chain):
            gzp, raw = work / f"p{i}.gz", work / f"p{i}.db"
            download(p, gzp, log)
            dbpatch.gunzip(gzp, raw)
            dbpatch.apply_patch(db, raw)
        got = dbpatch.dict_version(db)
        if got != target:
            raise ValueError(f"업데이트 뒤 판이 {got} (기대 {target})")
        os.replace(db, PENDING)
        result["dict"] = target
        log(f"사전 판 {target} 받음 (다음에 켤 때 적용)")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return result


def apply_pending(log=print):
    """앱 시작 때, pc.db를 열기 전에: 받아 둔 새 사전으로 바꾼다. 바꿨으면 그 판 번호."""
    if not PENDING.exists():
        return None
    ver = None
    try:
        ver = dbpatch.dict_version(PENDING)
        if not ver:
            raise ValueError("판 번호 없음")
        datastore.close_all()
        os.replace(PENDING, datastore.PCDB)
        log(f"사전 업데이트 적용: 판 {ver}")
        return ver
    except Exception as e:  # noqa: BLE001
        log(f"받아 둔 사전을 쓰지 못해 지움: {e}")
        PENDING.unlink(missing_ok=True)
        return None
