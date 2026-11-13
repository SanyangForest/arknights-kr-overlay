"""기록된 화면 하나의 OCR 줄 좌표를 출력한다 (줄 합치기 점검용).

사용법: python inspect_lines.py <ocr.json> [포함할 글자...]
"""

import json
import sys

from layout import to_box

sys.stdout.reconfigure(encoding="utf-8")
data = json.load(open(sys.argv[1], encoding="utf-8"))
keys = sys.argv[2:]
for it in sorted((to_box(i) for i in data), key=lambda b: (b["y0"], b["x0"])):
    if not keys or any(k in it["text"] for k in keys):
        print(f"x0={it['x0']:5.0f} y0={it['y0']:5.0f} x1={it['x1']:5.0f} y1={it['y1']:5.0f} "
              f"h={it['y1'] - it['y0']:3.0f}  {it['text']}")
