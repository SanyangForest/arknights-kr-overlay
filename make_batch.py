"""직접 번역할 묶음을 만든다: 기록된 게임 화면에 실제로 나왔는데 번역이 없는 원문 + 자주 나오는 새 고유명사.

각 문장에는 이미 공식 번역이 있는 용어(glossary.tsv)를 참고용으로 붙인다 (번역 일관성용).

출력: batches/<이름>.tsv  (id, zh, 참고 용어, 출처)

사용법: python make_batch.py <이름> <기록 폴더...> [--names N]
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path

from tsvio import unesc

ROOT = Path(__file__).parent
HAN = re.compile(r"[\u4e00-\u9fff]")
TAG = re.compile(r"<[@$/][^<>]{0,40}>|</?color[^<>]{0,20}>|</>")


def norm(s):
    return re.sub(r"[\s，。、：；！？…·,.:;!?\"'“”‘’（）()\[\]【】<>《》—\-]", "", TAG.sub("", s.replace("\\n", "")))


def read_tsv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("folders", nargs="+", type=Path)
    ap.add_argument("--names", type=int, default=200, help="자주 나오는 새 고유명사 몇 개를 넣을지")
    ap.add_argument("--max-len", type=int, default=200, help="이보다 긴 원문은 제외 (긴 스토리 문단은 따로)")
    args = ap.parse_args()

    # 화면에 나온 글자 전체 (OCR 결과)
    screen = ""
    for folder in args.folders:
        for f in sorted(folder.glob("frame_*.ocr.json")):
            screen += "|" + "|".join(norm(t) for _, t, _ in json.loads(f.read_text(encoding="utf-8")))

    done = set()
    for tsv in (ROOT / "extra").glob("*.tsv"):
        done |= {r["zh"] for r in read_tsv(tsv)}

    rows = []
    for r in read_tsv(ROOT / "out/untranslated.tsv"):
        zh = unesc(r["zh"])
        n = norm(zh)
        if zh in done or len(n) < 2 or len(zh) > args.max_len or not HAN.search(n):
            continue
        if n in screen:
            rows.append((zh, "화면", r["locations"]))
    on_screen = len(rows)
    for r in read_tsv(ROOT / "out/glossary_new.tsv")[:args.names]:
        zh = unesc(r["zh"])
        if zh not in done and all(zh != x[0] for x in rows):
            rows.append((zh, "고유명사", r["kinds"]))

    # 참고 용어: 원문에 들어 있는 공식 번역 용어 (긴 것 우선, 최대 6개)
    gloss = sorted(((g["zh"], g["ko"]) for g in read_tsv(ROOT / "out/glossary.tsv") if len(g["zh"]) >= 2),
                   key=lambda x: -len(x[0]))
    out = ROOT / "batches" / f"{args.name}.tsv"
    out.parent.mkdir(exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as f:
        f.write("id\tzh\tterms\tsource\n")
        for i, (zh, why, where) in enumerate(rows, 1):
            terms = []
            rest = zh
            for gz, gk in gloss:
                if gz in rest and len(terms) < 6:
                    terms.append(f"{gz}={gk}")
                    rest = rest.replace(gz, "")
            esc = zh.replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t")
            f.write(f"{i}\t{esc}\t{'; '.join(terms)}\t{why}:{where[:60]}\n")
    print(f"{out.name}: 화면에 나온 미번역 {on_screen}개 + 고유명사 {len(rows) - on_screen}개 = {len(rows)}개")


if __name__ == "__main__":
    main()
