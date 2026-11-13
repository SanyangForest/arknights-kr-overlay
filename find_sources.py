"""화면에서 잘려 보인 조각(OCR)마다, 그 조각이 들어 있는 원문 전체를 번역 필요 목록에서 찾는다.

사용법: python find_sources.py <screen_missing.tsv> <출력 tsv>
"""

import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
TAG = re.compile(r"<[@$/][^<>]{0,40}>|</?color[^<>]{0,20}>|</>")


def norm(s):
    s = TAG.sub("", s.replace("\\n", ""))
    return re.sub(r"[\s，。、：；！？…·,.:;!?\"'“”‘’（）()\[\]【】<>《》—\-]", "", s)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    frags = []
    for line in open(sys.argv[1], encoding="utf-8-sig"):
        parts = line.rstrip("\n").split("\t")
        if len(parts) == 2 and len(norm(parts[1])) >= 6:
            frags.append(norm(parts[1]))
    with open(ROOT / "out/untranslated.tsv", encoding="utf-8", newline="") as f:
        sources = [(r["zh"], norm(r["zh"])) for r in csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE)]
    found = {}
    for frag in frags:
        hits = [zh for zh, n in sources if frag in n and len(n) <= 400]
        if 1 <= len(hits) <= 3:
            for zh in hits:
                found.setdefault(zh, frag)
    with open(sys.argv[2], "w", encoding="utf-8", newline="") as f:
        f.write("zh\tfragment\n")
        for zh, frag in found.items():
            f.write(f"{zh}\t{frag}\n")
    print(f"조각 {len(frags)}개 → 원문 {len(found)}개")


if __name__ == "__main__":
    main()
