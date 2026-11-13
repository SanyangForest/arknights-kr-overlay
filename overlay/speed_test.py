"""OCR 속도 비교: CPU vs GPU(DirectML), 영역별.

사용법: python speed_test.py <이미지> [cpu|gpu]
"""

import sys
import time

import numpy as np
from PIL import Image

from ocr_engine import make_ocr, providers

sys.stdout.reconfigure(encoding="utf-8")
img = Image.open(sys.argv[1]).convert("RGB")
gpu = (sys.argv[2] if len(sys.argv) > 2 else "gpu") == "gpu"
w, h = img.size
regions = {
    "전체 화면": img,
    "대사창 (이름~대사)": img.crop((int(w * 0.18), int(h * 0.84), int(w * 0.88), h)),
}

ocr = make_ocr(gpu=gpu)
try:
    print("실행 장치:", providers(ocr))
except AttributeError as e:
    print("실행 장치 확인 실패:", e)
for rname, im in regions.items():
    arr = np.asarray(im)
    ocr(arr)
    t = time.perf_counter()
    for _ in range(5):
        res, _ = ocr(arr)
    ms = (time.perf_counter() - t) / 5 * 1000
    texts = " / ".join(r[1] for r in (res or []))
    print(f"{'GPU' if gpu else 'CPU'} {rname:<14} {im.size[0]}x{im.size[1]:<5} {ms:6.0f}ms  {texts}")
