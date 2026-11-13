"""기록된 화면들을 screens.json의 화면별로 나눠 번역 상태를 정리한다.

화면마다: 나온 장수, 한자 글자 영역 수, 번역 방식별 개수, 번역 못 한 문구, 비슷한 문장·부분 번역(오역 의심).
어느 화면에도 안 맞는 장면은 'unknown'으로 모으고 대표 장면 이름을 남겨 화면 지도를 넓히는 데 쓴다.

사용법: python screen_report.py <사전 json> <기록 폴더>
출력: <기록 폴더>_screens.md
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
    folder = Path(sys.argv[2])
    screens = load_screens()
    names = {s["id"]: s["name"] for s in screens} | {"unknown": "알 수 없는 화면"}
    ocr = None

    def ocr_fn():
        nonlocal ocr
        if ocr is None:
            from ocr_engine import make_ocr
            ocr = make_ocr(gpu=True)
        return ocr

    frames_of = defaultdict(list)
    how = defaultdict(Counter)
    missing = defaultdict(Counter)
    risky = defaultdict(Counter)
    frames = sorted(folder.glob("frame_*.png"))
    for i, frame in enumerate(frames):
        raw = ocr_cached(ocr_fn, frame)
        w, _ = Image.open(frame).size
        lines = merge_lines(raw, w, matcher.can_continue, matcher.is_entry)
        sid = classify([t for _, t, _ in raw], screens)
        frames_of[sid].append(frame.stem)
        for m in lines:
            if not HAN.search(m["text"]):
                continue
            if matcher.is_noise(m["text"]):
                how[sid]["무시(아이콘+숫자)"] += 1
                continue
            hit = matcher.lookup(m["text"], sid, edge_of((m["x0"], m["y0"], m["x1"], m["y1"]), w))
            if not hit:
                how[sid]["없음"] += 1
                missing[sid][m["text"]] += 1
                continue
            how[sid][hit[0]] += 1
            if hit[0] in ("fuzzy", "fuzzy_prefix", "segment", "confuse", "title", "edge"):
                risky[sid][(hit[0], m["text"], hit[2])] += 1
        print(f"\r{i + 1}/{len(frames)}", end="", flush=True)
    print()

    order = [s["id"] for s in screens] + ["unknown"]
    out = [f"# 화면별 점검: {folder.name} ({len(frames)}장)", "",
           "| 화면 | 장수 | 글자 영역 | 번역 못 함 | 오역 의심 |", "|---|---:|---:|---:|---:|"]
    for sid in order:
        if sid not in frames_of:
            continue
        total = sum(how[sid].values())
        miss = how[sid]["없음"]
        out.append(f"| {names[sid]} (`{sid}`) | {len(frames_of[sid])} | {total} | {miss} ({miss / max(total, 1):.0%}) | {sum(risky[sid].values())} |")
    for sid in order:
        if sid not in frames_of:
            continue
        fs = frames_of[sid]
        out += ["", f"## {names[sid]} (`{sid}`)", "", f"장면: {fs[0]} … {fs[-1]} ({len(fs)}장)", "",
                "방식: " + ", ".join(f"{k} {v}" for k, v in how[sid].most_common())]
        if missing[sid]:
            out += ["", "번역 못 한 문구:", ""] + [f"- {t!r} ×{n}" for t, n in missing[sid].most_common(40)]
        if risky[sid]:
            out += ["", "오역 의심 (비슷한 문장·부분 번역):", ""]
            out += [f"- [{h}] {t!r} → {k!r} ×{n}" for (h, t, k), n in risky[sid].most_common(40)]
    path = folder.with_name(folder.name + "_screens.md")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("→", path)


if __name__ == "__main__":
    main()
