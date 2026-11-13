"""병렬 번역용 묶음(JSON)을 만든다.

  names  : 새 고유명사(glossary_new.tsv) → chunks/names_XX.json
  content: 번역 필요 문장(untranslated.tsv) → chunks/content_XXX.json
           스토리는 파일 안의 대사 순서와 화자를 유지하고, 데이터 표는 표별로 묶는다.

각 묶음에는 그 안에 나오는 공식 용어(glossary.tsv)와 확정된 새 고유명사(extra/claude_names*.tsv)를 붙인다.
번역 결과는 같은 이름의 .out.partNN.jsonl({"id", "ko"} 한 줄씩)로 받는다.
이미 만든 묶음에 있는 원문은 진행 중이어도 다시 넣지 않는다.

사용법:
  python prep_chunks.py names   <공개 데이터 경로> [--parts 2]
  python prep_chunks.py content <공개 데이터 경로> [--chars 30000]
"""

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

from build_dict import HAN, NON_SCRIPT_FILES, story_tokens
from tsvio import unesc

ROOT = Path(__file__).parent
OUT = ROOT / "chunks"

def read_tsv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE))


def already_done():
    """extra/의 번역과 이미 만든 묶음(진행 중 포함)에 있는 원문."""
    done = set()
    for tsv in (ROOT / "extra").glob("*.tsv"):
        done |= {unesc(r["zh"]) for r in read_tsv(tsv)}
    for src in OUT.glob("*.json"):
        done |= {it["zh"] for it in json.loads(src.read_text(encoding="utf-8"))["items"]}
    return done


def load_glossary():
    """(우선 이름 목록, 공식 용어 목록). 각각 긴 용어 우선.

    우선 이름(커뮤니티 표기, 확정된 새 고유명사)은 개수 제한 없이 모두 붙이고,
    공식 용어는 제한 안에서 붙인다. 짧은 이름이 긴 용어에 밀려 빠지지 않도록.
    """
    names = {}
    for r in read_tsv(ROOT / "term_rules.tsv"):
        names[unesc(r["zh"])] = unesc(r["ko"])
    for pattern in ("community_*.tsv", "claude_names*.tsv", "claude_batch*.tsv"):
        for tsv in sorted((ROOT / "extra").glob(pattern)):
            for r in read_tsv(tsv):
                names.setdefault(unesc(r["zh"]), unesc(r["ko"]))
    official = {}
    for r in read_tsv(ROOT / "out/glossary.tsv"):
        zh = unesc(r["zh"])
        if len(zh) >= 2 and r["ko"] and zh not in names:
            official[zh] = unesc(r["ko"])
    by_len = lambda d: sorted(d.items(), key=lambda x: -len(x[0]))  # noqa: E731
    return by_len(names), by_len(official)


def chunk_glossary(texts, glossary, limit=250):
    names, official = glossary
    blob = "\n".join(texts)
    found = [{"zh": zh, "ko": ko} for zh, ko in names if zh in blob]
    n = 0
    for zh, ko in official:
        if n >= limit:
            break
        if zh in blob:
            found.append({"zh": zh, "ko": ko})
            n += 1
    return found


def write_chunk(name, kind, items, glossary, limit):
    for i, it in enumerate(items, 1):
        it["id"] = i
    payload = {"kind": kind, "items": items, "glossary": chunk_glossary([x["zh"] for x in items], glossary, limit)}
    (OUT / name).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def make_names(parts):
    done = already_done()
    rows = [r for r in read_tsv(ROOT / "out/glossary_new.tsv") if unesc(r["zh"]) not in done]
    OUT.mkdir(exist_ok=True)
    glossary = load_glossary()
    size = (len(rows) + parts - 1) // parts
    first = len(list(OUT.glob("names_*.json"))) + 1
    for n, start in enumerate(range(0, len(rows), size), first):
        items = [{"zh": unesc(r["zh"]), "context": r["kinds"]} for r in rows[start:start + size]]
        write_chunk(f"names_{n:02d}.json", "names", items, glossary, 400)
        print(f"names_{n:02d}.json: {len(items)}개")


def make_content(data_root, max_chars):
    done = already_done()
    todo = {}
    for r in read_tsv(ROOT / "out/untranslated.tsv"):
        zh = unesc(r["zh"])
        if zh not in done and HAN.search(zh):
            todo[zh] = r["locations"]

    # 1) 스토리: 파일 순서대로, 직전 화자 포함
    groups = defaultdict(list)
    placed = set()
    story_dir = Path(data_root) / "cn/gamedata/story"
    for f in sorted(story_dir.rglob("*.txt")):
        if f.name in NON_SCRIPT_FILES:
            continue
        rel = f.relative_to(story_dir).as_posix()
        group = "story/" + "/".join(rel.split("/")[:2])
        speaker = ""
        for _, texts in story_tokens(f.read_text(encoding="utf-8-sig")):
            for kind, t in texts:
                if kind == "name":
                    speaker = t
                    continue
                if t in todo and t not in placed:
                    groups[group].append({"zh": t, "speaker": speaker, "file": rel})
                    placed.add(t)
    # 2) 데이터 표 등: 첫 위치별
    for zh, locs in todo.items():
        if zh not in placed:
            first = locs.split(",")[0]
            groups[first if first.startswith("excel/") else "misc"].append({"zh": zh, "file": first})
            placed.add(zh)

    # 3) 묶음으로 자르기 (최근 이벤트 스토리부터)
    OUT.mkdir(exist_ok=True)
    glossary = load_glossary()
    order = sorted(groups, key=lambda g: (not g.startswith("story/activities"), g))
    chunks, cur, cur_chars = [], [], 0
    for group in order:
        for item in groups[group]:
            n = len(item["zh"])
            if cur and cur_chars + n > max_chars:
                chunks.append(cur)
                cur, cur_chars = [], 0
            cur.append(item)
            cur_chars += n
    if cur:
        chunks.append(cur)
    start = len(list(OUT.glob("content_*.json"))) + 1
    for n, items in enumerate(chunks, start):
        write_chunk(f"content_{n:03d}.json", "content", items, glossary, 250)
    total = sum(len(x["zh"]) for c in chunks for x in c)
    print(f"묶음 {len(chunks)}개 (content_{start:03d}~), 문장 {sum(len(c) for c in chunks):,}개, {total:,}자")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["names", "content"])
    ap.add_argument("data_root")
    ap.add_argument("--chars", type=int, default=30000)
    ap.add_argument("--parts", type=int, default=2)
    args = ap.parse_args()
    if args.mode == "names":
        make_names(args.parts)
    else:
        make_content(args.data_root, args.chars)


if __name__ == "__main__":
    main()
