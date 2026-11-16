"""스토리 대본 색인: 중국 서버 스토리 파일을 대사 순서대로 펼쳐 한국어와 함께 out/story.db(SQLite)에 넣는다.

오버레이의 스토리 따라가기(overlay/story.py)가 쓴다. 화면 대사 한두 줄로 어느 스토리의 몇 번째 대사인지 찾은 뒤,
다음 대사들은 이 순서대로 공식 한국어 대본에서 꺼내 보여 준다 (줄바꿈·글자 하나씩 나오기·OCR 오독에 흔들리지 않음).

  units(file, idx, grp, kind, name, name_ko, norm, ko)
    idx  : 파일 안에서 화면에 나오는 글의 순서 (선택지는 하나씩 따로, 같은 grp)
    kind : dialog(화자 있는 대사) / narration / subtitle / sticker / decision / multiline / ...
    norm : 중국어를 story.core로 바꾼 것 (normalize + 앞뒤 문장부호 뗌, 찾기용 색인)
    ko   : 한국어 (한국 서버 파일과 같은 자리 → 없으면 사전 out/dict_zh_ko.json)
  files(id, path, code, title, title_ko, tag, tag_ko)  : 스토리 다시보기 표(story_review_table)의 이름

사용법: python build_story.py <ArknightsGamedata 경로> [--out out]
"""

import argparse
import difflib
import json
import sqlite3
import sys
import time
from pathlib import Path

from build_dict import NON_SCRIPT_FILES, needs_translation, story_tokens

sys.path.insert(0, str(Path(__file__).resolve().parent / "overlay"))
from matcher import TAG  # noqa: E402
from story import core  # noqa: E402


def review_titles(root):
    """스토리 파일 경로 → (코드, 중국어 제목, 한국어 제목, 태그, 한국어 태그)."""
    def load(region):
        p = root / region / "gamedata/excel/story_review_table.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    cn, kr = load("cn"), load("kr")
    kr_info = {u["storyId"]: (g, u) for g, v in kr.items() for u in v.get("infoUnlockDatas", [])}
    out = {}
    for gid, v in cn.items():
        for u in v.get("infoUnlockDatas", []):
            if not u.get("storyTxt"):
                continue
            kg, ku = kr_info.get(u["storyId"], (None, {}))
            title = f"{v.get('name') or ''} {u.get('storyName') or ''}".strip()
            title_ko = f"{(kr.get(kg) or {}).get('name') or ''} {ku.get('storyName') or ''}".strip() if ku else ""
            out[u["storyTxt"]] = (u.get("storyCode") or "", title, title_ko, u.get("avgTag") or "", ku.get("avgTag") or "")
    return out


def units_of(zh_tok, ko_tok, dict_ko):
    """CN 파일 토큰을 화면 글 단위로 펼친다. 한국어는 같은 자리의 KR 토큰, 없으면 사전."""
    pair = {}
    if ko_tok is not None:
        sm = difflib.SequenceMatcher(None, [t[0] for t in zh_tok], [t[0] for t in ko_tok], autojunk=False)
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == "equal":
                for i, j in zip(range(i1, i2), range(j1, j2)):
                    pair[i] = ko_tok[j][1]
    out = []
    grp = 0
    for i, (kind_key, texts) in enumerate(zh_tok):
        if not texts:
            continue
        kind = kind_key.split(":")[1] if kind_key.startswith("TEXT:") else kind_key
        kt = pair.get(i)
        if kt is not None and len(kt) != len(texts):
            kt = None
        name = name_ko = ""
        for j, (field, z) in enumerate(texts):
            if field == "name" and kind in ("dialog", "multiline"):
                name, name_ko = z, (kt[j][1] if kt else dict_ko.get(z, ""))
        grp += 1
        for j, (field, z) in enumerate(texts):
            if field == "name" and kind in ("dialog", "multiline"):
                continue
            if not needs_translation(z):
                continue
            ko = kt[j][1] if kt else dict_ko.get(z, "")
            if not ko:
                ko = dict_ko.get(z.strip(), "")
            out.append((grp, kind, name, name_ko, z, ko))
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--out", default="out")
    a = ap.parse_args()
    root, out = Path(a.root), Path(a.out)
    t0 = time.time()
    dict_ko = json.loads((out / "dict_zh_ko.json").read_text(encoding="utf-8"))
    titles = review_titles(root)
    tmp = out / "story.db.tmp"
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.executescript("""
        CREATE TABLE files(id INTEGER PRIMARY KEY, path TEXT, code TEXT, title TEXT, title_ko TEXT, tag TEXT, tag_ko TEXT);
        CREATE TABLE units(file INTEGER, idx INTEGER, grp INTEGER, kind TEXT, name TEXT, name_ko TEXT, norm TEXT, ko TEXT,
                           PRIMARY KEY(file, idx)) WITHOUT ROWID;
    """)
    cn_dir, kr_dir = root / "cn/gamedata/story", root / "kr/gamedata/story"
    n_files = n_units = n_missing = 0
    for fid, cn_file in enumerate(sorted(cn_dir.rglob("*.txt")), 1):
        rel = cn_file.relative_to(cn_dir).as_posix()
        if cn_file.name in NON_SCRIPT_FILES or rel.startswith("[uc]info/"):
            continue
        kr_file = kr_dir / rel
        zh_tok = story_tokens(cn_file.read_text(encoding="utf-8-sig"))
        ko_tok = story_tokens(kr_file.read_text(encoding="utf-8-sig")) if kr_file.exists() else None
        rows = []
        for idx, (grp, kind, name, name_ko, z, ko) in enumerate(units_of(zh_tok, ko_tok, dict_ko)):
            norm = core(z)
            if not norm:
                continue
            n_missing += not ko
            rows.append((fid, idx, grp, kind, core(name), TAG.sub("", name_ko), norm, ko))
        if not rows:
            continue
        key = rel[:-4]
        code, title, title_ko, tag, tag_ko = titles.get(key, ("", "", "", "", ""))
        db.execute("INSERT INTO files VALUES (?,?,?,?,?,?,?)", (fid, key, code, title, title_ko, tag, tag_ko))
        db.executemany("INSERT INTO units VALUES (?,?,?,?,?,?,?,?)", rows)
        n_files += 1
        n_units += len(rows)
    db.execute("CREATE INDEX units_norm ON units(norm)")
    db.commit()
    db.execute("VACUUM")
    db.close()
    final = out / "story.db"
    final.unlink(missing_ok=True)
    tmp.rename(final)
    print(f"스토리 {n_files}개 파일, 글 {n_units:,}줄 (한국어 없음 {n_missing:,}) → {final} "
          f"{final.stat().st_size / 1e6:.0f}MB, {time.time() - t0:.0f}초")


if __name__ == "__main__":
    main()
