"""묶음별 번역 진행 상황 (결과 파일에 있는 id 수 / 전체)."""

import glob
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
done_items = total_items = 0
for f in sorted(glob.glob("chunks/*.json")):
    stem = os.path.basename(f)[:-5]
    n = len(json.load(open(f, encoding="utf-8"))["items"])
    ids = set()
    for p in glob.glob(f"chunks/{stem}.out.part*.jsonl"):
        for line in open(p, encoding="utf-8"):
            try:
                ids.add(json.loads(line)["id"])
            except (ValueError, KeyError):
                pass
    done_items += len(ids)
    total_items += n
    state = "완료" if len(ids) >= n else ("진행 중" if ids else "시작 전")
    print(f"{stem}: {len(ids)}/{n} {state}")
print(f"전체: {done_items:,}/{total_items:,} ({done_items / total_items:.0%})")
