"""보유 오퍼레이터: 오퍼레이터 목록 화면에서 읽은 이름을 저장해 두고 공개모집 패널 등에 쓴다.

overlay/data/owned.json = {"updated": "...", "ops": {char_id: 중국어 이름}}
오퍼레이터 목록 화면을 사용자가 넘기는 동안 오버레이가 보이는 이름을 기록한다 (overlay.Worker.record).
"""

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OWNED = ROOT / "data" / "owned.json"
GAME = ROOT.parent / "data" / "gamedata"


def char_names():
    """중국어 이름 → char_id (플레이 가능한 오퍼레이터만)."""
    chars = json.loads((GAME / "cn/gamedata/excel/character_table.json").read_text(encoding="utf-8"))
    out = {}
    for cid, c in chars.items():
        if cid.startswith("char_") and c.get("name") and not c.get("isNotObtainable") and c.get("profession") != "TOKEN":
            out.setdefault(c["name"], cid)
    return out


def load():
    """{char_id: 이름} (저장한 적 없으면 빈 dict)."""
    try:
        return json.loads(OWNED.read_text(encoding="utf-8"))["ops"]
    except (OSError, ValueError, KeyError):
        return {}


def save(ops):
    OWNED.parent.mkdir(parents=True, exist_ok=True)
    OWNED.write_text(json.dumps({"updated": time.strftime("%Y-%m-%d %H:%M"), "ops": ops},
                                ensure_ascii=False, indent=1), encoding="utf-8")


class Owned:
    """파일이 바뀌면 다시 읽는 보유 목록."""

    def __init__(self):
        self.mtime, self.ids = None, set()
        self.refresh()

    def refresh(self):
        try:
            m = OWNED.stat().st_mtime
        except OSError:
            return
        if m != self.mtime:
            self.mtime, self.ids = m, set(load())

    def __contains__(self, cid):
        return cid in self.ids

    def __bool__(self):
        return bool(self.ids)
