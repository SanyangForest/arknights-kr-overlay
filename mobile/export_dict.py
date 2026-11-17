"""휴대폰 앱용 사전(SQLite)을 만든다. PC판 Matcher가 읽어 들인 표를 그대로 옮긴다.

휴대폰은 메모리가 작아 PC처럼 사전 전체(54만 문장)를 메모리에 올리지 않고, 필요한 문장만 찾아 읽는다.
키는 PC판 normalize()를 거친 모양 그대로이므로, 앱의 정규화도 PC판과 똑같아야 한다.

  entries(key, ko)        정규화한 원문 → 화면에 보일 번역 (서식 태그 지움)
  skel(key, zh, ko)       숫자·변수를 #로 바꾼 틀 → 원문 틀, 번역 틀 (숫자 채우기용)
  rev(rkey, key)          짧은 문장(24자 이하)의 거꾸로 쓴 키: 첫 글자를 틀리게 읽은 OCR도 끝 글자로 후보를 찾음
  aliases(screen, ocr, zh) 화면별 OCR 오독 → 원래 문구 (ocr_aliases.tsv)
  meta(k, v)              만든 시각, 문장 수, 박사 이름(overlay/settings.json의 nickname)
  recruit_tags(id, zh, ko)          공개모집 태그 (out/recruit.json, build_recruit.py가 만듦)
  recruit_ops(id, ko, rarity, tags) 모집 가능 오퍼레이터, tags는 쉼표로 이은 태그 id
  recruit_rule(k, v)                시간별 등급 범위('times': '분,최저,최고' 줄), 특별 태그('special': '태그=등급')

사용법: python export_dict.py [출력 db 경로]
"""

import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "overlay"))

from matcher import TAG, Matcher, display  # noqa: E402

DEFAULT_OUT = Path(__file__).with_name("app") / "src" / "main" / "assets" / "dict.db"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    m = Matcher(str(ROOT / "out" / "dict_zh_ko.json"))
    db = sqlite3.connect(out)
    db.executescript("""
        PRAGMA page_size = 4096;
        CREATE TABLE entries (key TEXT PRIMARY KEY, ko TEXT NOT NULL) WITHOUT ROWID;
        CREATE TABLE skel (key TEXT PRIMARY KEY, zh TEXT NOT NULL, ko TEXT NOT NULL) WITHOUT ROWID;
        CREATE TABLE rev (rkey TEXT PRIMARY KEY, key TEXT NOT NULL) WITHOUT ROWID;
        CREATE TABLE aliases (screen TEXT, ocr TEXT, zh TEXT NOT NULL, PRIMARY KEY (screen, ocr)) WITHOUT ROWID;
        CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT) WITHOUT ROWID;
        CREATE TABLE levels (key TEXT PRIMARY KEY, sets TEXT NOT NULL) WITHOUT ROWID;
        CREATE TABLE skel_multi (key TEXT PRIMARY KEY) WITHOUT ROWID;
        CREATE TABLE recruit_tags (id INTEGER PRIMARY KEY, zh TEXT NOT NULL, ko TEXT NOT NULL);
        CREATE TABLE recruit_ops (id TEXT PRIMARY KEY, ko TEXT NOT NULL, rarity INTEGER NOT NULL, tags TEXT NOT NULL) WITHOUT ROWID;
        CREATE TABLE recruit_rule (k TEXT PRIMARY KEY, v TEXT NOT NULL) WITHOUT ROWID;
    """)
    db.executemany("INSERT INTO entries VALUES (?, ?)", ((k, display(ko)) for k, (_, ko) in m.table.items()))
    # 숫자 틀은 _fill이 원문의 숫자·변수 자리를 세어 채우므로 원문 틀도 함께 (번역 틀은 태그만 지움)
    db.executemany("INSERT INTO skel VALUES (?, ?, ?)", ((k, TAG.sub("", zh), display(ko)) for k, (zh, ko) in m.skel.items()))
    db.executemany("INSERT OR IGNORE INTO rev VALUES (?, ?)", ((k[::-1], k) for k in m.table if len(k) <= 24))
    db.executemany("INSERT INTO aliases VALUES (?, ?, ?)", ((s, o, z) for (s, o), z in m.aliases.items()))
    # 스킬 설명 틀별 레벨 수치: 한 줄에 한 레벨 '이름=값,이름=값' (폰의 JVM 시험에서도 읽게 JSON 대신 단순한 글)
    db.executemany("INSERT INTO levels VALUES (?, ?)", (
        (k, "\n".join(",".join(f"{n}={float(v)!r}" for n, v in vals.items()) for vals in sets))
        for k, sets in m.levels.items()))
    db.executemany("INSERT INTO skel_multi VALUES (?)", ((k,) for k in m.skel_multi))
    recruit = ROOT / "out" / "recruit.json"
    if recruit.exists():
        r = json.loads(recruit.read_text(encoding="utf-8"))
        db.executemany("INSERT INTO recruit_tags VALUES (?, ?, ?)", ((t["id"], t["zh"], t["ko"]) for t in r["tags"]))
        db.executemany("INSERT INTO recruit_ops VALUES (?, ?, ?, ?)",
                       ((o["id"], o["ko"], o["rarity"], ",".join(map(str, o["tags"]))) for o in r["ops"]))
        db.executemany("INSERT INTO recruit_rule VALUES (?, ?)", [
            ("times", "\n".join(",".join(map(str, t)) for t in r["times"])),
            ("special", "\n".join(f"{k}={','.join(map(str, v))}" for k, v in r["special"].items()))])
    else:
        print("공개모집 데이터 없음 (python build_recruit.py <게임 데이터>를 먼저 실행)")
    db.executemany("INSERT INTO meta VALUES (?, ?)", [
        ("built", time.strftime("%Y-%m-%d %H:%M")), ("entries", str(len(m.table))), ("skel", str(len(m.skel))), ("nickname", m.nickname or ""),
        # 흘러가는 안내 문구 (overlay/marquee.tsv): 화면 끝에서 잘린 조각도 전체 문장으로 번역
        ("marquee", "\n".join(f for f in m.marquee if f in m.table)),
        # 오퍼레이터 이름 (채용 결과 '직군 + 이름' 규칙에서 이름이 오퍼레이터인지 확인)
        ("op_names", "\n".join(sorted(n for n in m.op_names if n in m.table)))])
    db.commit()
    db.execute("VACUUM")
    db.close()
    print(f"{out}: 문장 {len(m.table):,}, 숫자 틀 {len(m.skel):,}, 별칭 {len(m.aliases):,}, "
          f"{out.stat().st_size / 1e6:.0f}MB")


if __name__ == "__main__":
    main()
