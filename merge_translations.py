"""에이전트 번역 결과(chunks/*.out.part*.jsonl)를 검증해서 extra/claude_<묶음>.tsv로 만든다.

검증 (하나라도 어기면 버리고 rejects에 기록):
  - id가 원문 묶음에 있고, 번역이 비어 있지 않음
  - 번역에 한자가 남아 있지 않음
  - 서식 태그(<@...>, </>, <color=...> 등)와 변수({...})가 원문과 같음
  - 원문의 숫자가 번역에 모두 있음

사용법: python merge_translations.py [묶음 이름 접두어...]   (예: names, content)
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

from tsvio import esc

ROOT = Path(__file__).parent
CHUNKS = ROOT / "chunks"
HAN = re.compile(r"[\u4e00-\u9fff]")
TAG = re.compile(r"<[@$/][^<>]{0,40}>|</?color[^<>]{0,20}>|</>|</?[ib]>")
PLACEHOLDER = re.compile(r"\{[^{}]*\}")
DIGITS = re.compile(r"\d+")


def check(zh, ko):
    if not isinstance(ko, str) or not ko.strip():
        return "빈 번역"
    if HAN.search(TAG.sub("", ko)):
        return "한자 남음"
    if Counter(TAG.findall(zh)) != Counter(TAG.findall(ko)):
        return "서식 태그 다름"
    if Counter(PLACEHOLDER.findall(zh)) != Counter(PLACEHOLDER.findall(ko)):
        return "변수 다름"
    if Counter(DIGITS.findall(zh)) - Counter(DIGITS.findall(ko)):
        return "숫자 빠짐"
    return None


def merge(src):
    items = {it["id"]: it for it in json.loads(src.read_text(encoding="utf-8"))["items"]}
    parts = sorted(CHUNKS.glob(src.stem + ".out.part*.jsonl"))
    if not parts:
        return None
    got, rejects = {}, []
    for part in parts:
        for n, line in enumerate(part.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                sid, ko = int(row["id"]), row["ko"]
            except (ValueError, KeyError, TypeError):
                rejects.append((part.name, n, "JSON 오류", line[:80]))
                continue
            if sid not in items:
                rejects.append((part.name, n, "없는 id", line[:80]))
                continue
            reason = check(items[sid]["zh"], ko)
            if reason:
                rejects.append((part.name, sid, reason, f"{items[sid]['zh'][:40]} → {str(ko)[:40]}"))
                continue
            got[sid] = ko
    out = ROOT / "extra" / f"claude_{src.stem}.tsv"
    with open(out, "w", encoding="utf-8", newline="") as f:
        f.write("zh\tko\tsource\n")
        for sid in sorted(got):
            f.write(f"{esc(items[sid]['zh'])}\t{esc(got[sid])}\tclaude\n")
    missing = sorted(set(items) - set(got))
    with open(CHUNKS / f"{src.stem}.rejects.tsv", "w", encoding="utf-8", newline="") as f:
        f.write("file\tid\treason\tsample\n")
        for r in rejects:
            f.write("\t".join(map(str, r)) + "\n")
        for sid in missing:
            if not any(r[1] == sid for r in rejects):
                f.write(f"-\t{sid}\t누락\t{items[sid]['zh'][:40]}\n")
    return len(items), len(got), len(rejects), len(missing)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    prefixes = sys.argv[1:] or ["names", "content"]
    total = Counter()
    for src in sorted(CHUNKS.glob("*.json")):
        if not any(src.stem.startswith(p) for p in prefixes):
            continue
        res = merge(src)
        if res is None:
            print(f"{src.stem}: 결과 없음")
            continue
        n, ok, bad, miss = res
        total.update(items=n, ok=ok, bad=bad, miss=miss)
        print(f"{src.stem}: {ok}/{n} 통과, 거부 {bad}, 누락 {miss}")
    print(f"합계: {total['ok']}/{total['items']} 통과, 거부 {total['bad']}, 누락 {total['miss']}")


if __name__ == "__main__":
    main()
