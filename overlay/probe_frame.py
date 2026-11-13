"""기록된 화면 한 장의 OCR 줄과 매칭 결과를 그대로 보여준다 (화면별 점검용).

사용법: python probe_frame.py <사전 json> <frame_XXXX.png> [포함할 글자...]
"""

import sys
from pathlib import Path

from PIL import Image

from batch_eval import ocr_cached
from layout import merge_lines
from matcher import HAN, Matcher, edge_of
from screens import classify, load_screens


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    matcher = Matcher(sys.argv[1])
    frame = Path(sys.argv[2])
    keys = sys.argv[3:]
    ocr = None

    def ocr_fn():
        nonlocal ocr
        if ocr is None:
            from ocr_engine import make_ocr
            ocr = make_ocr(gpu=True)
        return ocr

    raw = ocr_cached(ocr_fn, frame)
    w, _ = Image.open(frame).size
    screen = classify([t for _, t, _ in raw], load_screens())
    print("화면:", screen)
    for m in sorted(merge_lines(raw, w, matcher.can_continue, matcher.is_entry,
                                lambda t: matcher.lookup(t, screen) is not None), key=lambda m: (m["y0"], m["x0"])):
        text = m["text"]
        if keys and not any(k in text for k in keys):
            continue
        if not HAN.search(text):
            continue
        hit = matcher.lookup(text, screen, edge_of((m["x0"], m["y0"], m["x1"], m["y1"]), w))
        res = f"{hit[0]:12s} {hit[2]!r}" if hit else "없음"
        print(f"({m['x0']:4.0f},{m['y0']:4.0f}) h={m['y1'] - m['y0']:3.0f} {text!r:30s} → {res}")
    # 줄 합치기 전 원래 OCR 결과 중 한자가 있는데 위에서 안 보인 것 (작은 글자, 낮은 점수 등)
    print("--- OCR 원본 (점수)")
    for box, text, score in raw:
        if HAN.search(text) and (not keys or any(k in text for k in keys)):
            print(f"  {score:.2f} ({box[0][0]:4.0f},{box[0][1]:4.0f}) {text!r}")


if __name__ == "__main__":
    main()
