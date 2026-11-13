"""OCR 검출 설정을 바꿔 가며 화면 한 장에서 어떤 글자가 잡히는지 비교한다.

사용법: python ocr_tune.py <frame.png> [찾을 글자...]
"""

import sys
import time

import numpy as np
from PIL import Image

from matcher import HAN
from ocr_engine import make_ocr

SETTINGS = [
    ("기본 1280", 1280, {}),
    ("1600", 1600, {}),
    ("1600 낮은 문턱", 1600, {"thresh": 0.2, "box_thresh": 0.4}),
    ("1600 낮은 문턱+넓게", 1600, {"thresh": 0.2, "box_thresh": 0.4, "unclip_ratio": 2.0}),
]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    img = np.asarray(Image.open(sys.argv[1]).convert("RGB"))
    want = sys.argv[2:]
    for name, limit, post in SETTINGS:
        ocr = make_ocr(gpu=True, limit_side_len=limit)
        for k, v in post.items():
            setattr(ocr.text_detector.postprocess_op, k, v)
        ocr(img)  # 첫 실행은 준비 시간이 섞이므로 버림
        t = time.perf_counter()
        result = ocr(img)[0] or []
        ms = (time.perf_counter() - t) * 1000
        texts = [r[1] for r in result if HAN.search(r[1])]
        found = [w for w in want if any(w in x for x in texts)]
        print(f"[{name}] {ms:.0f}ms, 한자 줄 {len(texts)}개, 찾음 {found}")
        if not want:
            print("   ", texts)


if __name__ == "__main__":
    main()
