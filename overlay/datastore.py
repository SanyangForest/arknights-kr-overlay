"""데이터 읽기: 개발에서는 out/·data/gamedata의 파일들, 배포판에서는 data/pc.db 하나.

pc.db (build_pcdb.py가 만듦, 자동 업데이트(updater.py)가 휴대폰 사전과 같은 방식의 패치로 고침):
  meta(k, v)          dict_version(올린 판 번호), built
  dict(zh, ko)        사전 (out/dict_zh_ko.json)
  blobs(name, data)   작은 파일 통째 (glossary.tsv, skill_levels.json, recruit.json, screen_vocab.json,
                      excel/<cn|kr>/<표>.json = 게임 표)
  units, files        스토리 대본 (out/story.db와 같은 표)
환경 변수 AKKR_PCDB=1이면 개발에서도 out/pc.db를 쓴다 (배포판과 같은지 시험할 때).
"""

import json
import os
import sqlite3

import paths

PCDB = paths.OUT / "pc.db"
USE_PCDB = paths.FROZEN or os.environ.get("AKKR_PCDB") == "1"

_conns = {}


def open_db(path=None):
    """읽기 전용 연결 (경로마다 하나, 여러 스레드에서 읽기만 함)."""
    path = str(path or PCDB)
    if path not in _conns:
        _conns[path] = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    return _conns[path]


def close_all():
    """업데이트로 pc.db를 바꾸기 전에 연결을 모두 닫는다."""
    for c in _conns.values():
        c.close()
    _conns.clear()


def blob(name, db=None):
    """pc.db의 작은 파일 내용 (없으면 None)."""
    row = (db or open_db()).execute("SELECT data FROM blobs WHERE name=?", (name,)).fetchone()
    return row[0] if row else None


def text(name):
    """out/ 의 작은 파일(glossary.tsv 등) 내용. 없으면 None."""
    if USE_PCDB:
        return blob(name) if PCDB.exists() else None
    p = paths.OUT / name
    return p.read_text(encoding="utf-8") if p.exists() else None


def json_file(name):
    t = text(name)
    return json.loads(t) if t is not None else None


def excel(name, region="cn"):
    """게임 표 (character_table 등)."""
    if USE_PCDB:
        return json.loads(blob(f"excel/{region}/{name}.json"))
    return json.loads(paths.excel(name, region).read_text(encoding="utf-8"))


def dict_path():
    """Matcher에 넘길 사전 위치: pc.db 또는 out/dict_zh_ko.json."""
    return PCDB if USE_PCDB else paths.OUT / "dict_zh_ko.json"


def story_db():
    return PCDB if USE_PCDB else paths.OUT / "story.db"


def version(path=None):
    """pc.db의 판 번호 (없으면 None)."""
    path = path or PCDB
    if not os.path.exists(path):
        return None
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = c.execute("SELECT v FROM meta WHERE k='dict_version'").fetchone()
        return row[0] if row else None
    finally:
        c.close()
