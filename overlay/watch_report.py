"""기록 폴더에서 N번째 장면부터만 골라, 번역 못 한 문구를 화면별로 정리한다 (사용자가 돌아다니는 동안 점검용).

사용법: python watch_report.py <사전 json> <기록 폴더> <시작 번호>
"""

import sys
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image

from batch_eval import ocr_cached
from layout import merge_lines
from matcher import HAN, Matcher, edge_of
from screens import classify, load_screens


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    matcher = Matcher(sys.argv[1])
    folder, start = Path(sys.argv[2]), int(sys.argv[3])
    screens = load_screens()
    ocr = None

    def ocr_fn():
        nonlocal ocr
        if ocr is None:
            from ocr_engine import make_ocr
            ocr = make_ocr(gpu=True)
        return ocr

    frames = [f for f in sorted(folder.glob("frame_*.png")) if int(f.stem.split("_")[1]) >= start]
    missing = defaultdict(Counter)
    first = {}
    totals = Counter()
    for f in frames:
        raw = ocr_cached(ocr_fn, f)
        w, _ = Image.open(f).size
        sid = classify([t for _, t, _ in raw], screens)
        totals[sid] += 1
        for m in merge_lines(raw, w, matcher.can_continue, matcher.is_entry):
            if not HAN.search(m["text"]) or matcher.is_noise(m["text"]):
                continue
            box = (m["x0"], m["y0"], m["x1"], m["y1"])
            if not matcher.lookup(m["text"], sid, edge_of(box, w)):
                missing[sid][m["text"]] += 1
                first.setdefault((sid, m["text"]), (f.stem, tuple(int(v) for v in box)))
    print(f"장면 {len(frames)}장: " + ", ".join(f"{k} {v}" for k, v in totals.most_common()))
    for sid, cnt in sorted(missing.items(), key=lambda x: -sum(x[1].values())):
        print(f"\n## {sid} (장면 {totals[sid]}장)")
        for text, n in cnt.most_common(30):
            stem, box = first[(sid, text)]
            print(f"  ×{n:<3} {text[:60]!r}  ({stem} {box})")


if __name__ == "__main__":
    main()
