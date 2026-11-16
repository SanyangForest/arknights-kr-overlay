"""PC 배포판 데이터 묶기: out/의 사전·대본·작은 파일과 게임 표를 SQLite 하나(out/pc.db)로.

배포판은 이 파일 하나만 읽고(overlay/datastore.py), 자동 업데이트는 휴대폰 사전처럼 바뀐 줄만 담은 패치
(mobile/make_patch.py)로 고친다. 사전(41MB)·대본(50MB)을 통째로 다시 받지 않게.

  python build_pcdb.py [--out out/pc.db] [--version 판번호]

먼저 build_dict.py · build_recruit.py · build_story.py (finalize.bat)와 overlay/vocab.py build가 끝나 있어야 한다.
"""

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"
GAME = ROOT / "data" / "gamedata"
BLOBS = ["glossary.tsv", "skill_levels.json", "recruit.json", "screen_vocab.json"]
# 정보 패널·육성 계산기가 읽는 게임 표 (overlay/drops.py, owned.py, planner.py)
TABLES = {"cn": ["stage_table", "item_table", "character_table", "skill_table", "gamedata_const"],
          "kr": ["item_table", "character_table", "skill_table"]}


def build(out, version=None):
    out = Path(out)
    tmp = out.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.executescript("""
        CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT) WITHOUT ROWID;
        CREATE TABLE dict (zh TEXT PRIMARY KEY, ko TEXT NOT NULL) WITHOUT ROWID;
        CREATE TABLE blobs (name TEXT PRIMARY KEY, data TEXT NOT NULL) WITHOUT ROWID;
    """)
    raw = json.loads((OUT / "dict_zh_ko.json").read_text(encoding="utf-8"))
    db.executemany("INSERT INTO dict VALUES (?, ?)", raw.items())
    for name in BLOBS:
        db.execute("INSERT INTO blobs VALUES (?, ?)", (name, (OUT / name).read_text(encoding="utf-8")))
    for region, names in TABLES.items():
        for n in names:
            data = (GAME / region / "gamedata" / "excel" / f"{n}.json").read_text(encoding="utf-8")
            db.execute("INSERT INTO blobs VALUES (?, ?)", (f"excel/{region}/{n}.json", data))
    # 스토리 대본: out/story.db의 표를 같은 구조로
    db.execute("ATTACH ? AS s", (str(OUT / "story.db"),))
    for t, sql in db.execute("SELECT name, sql FROM s.sqlite_master WHERE type='table'").fetchall():
        db.execute(sql)
        db.execute(f"INSERT INTO main.{t} SELECT * FROM s.{t}")
    for (sql,) in db.execute("SELECT sql FROM s.sqlite_master WHERE type='index' AND sql IS NOT NULL").fetchall():
        db.execute(sql)
    db.commit()
    db.execute("DETACH s")
    db.executemany("INSERT INTO meta VALUES (?, ?)", [
        ("dict_version", version or time.strftime("local-%Y%m%d%H%M")),
        ("built", time.strftime("%Y-%m-%d %H:%M")),
        ("entries", str(len(raw))),
    ])
    db.commit()
    db.execute("VACUUM")
    db.close()
    out.unlink(missing_ok=True)
    tmp.rename(out)
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT / "pc.db"))
    ap.add_argument("--version", default=None)
    a = ap.parse_args()
    t = time.time()
    out = build(a.out, a.version)
    print(f"{out} {out.stat().st_size / 1e6:.0f}MB, {time.time() - t:.0f}초")


if __name__ == "__main__":
    main()
