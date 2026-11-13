"""스크린샷 한 장에 오버레이와 같은 규칙으로 번역을 덮어 그려 확인한다 (게임 없이 오프라인 점검용).

출력: <이름>_debug.png (빨간 테두리 = 번역 못 한 글자 영역)

사용법: python debug_render.py <사전 json> <이미지...>
"""

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from fit import free_space, layout
from layout import merge_lines
from matcher import Matcher
from ocr_engine import make_ocr

FONT = "C:/Windows/Fonts/malgun.ttf"


class PilFont:
    """fit.layout이 쓰는 measure/metrics를 PIL 폰트로 흉내 낸다."""

    def __init__(self, size):
        self.font = ImageFont.truetype(FONT, size)
        self.size = size

    def measure(self, text):
        return self.font.getlength(text)

    def metrics(self, _):
        return int(self.size * 1.33)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    matcher = Matcher(sys.argv[1])
    ocr = make_ocr(gpu=True)
    fonts = {}

    def font_for(size):
        if size not in fonts:
            fonts[size] = PilFont(size)
        return fonts[size]

    for path in map(Path, sys.argv[2:]):
        img = Image.open(path).convert("RGB")
        arr = np.asarray(img)
        w, h = img.size
        lines = merge_lines(ocr(arr)[0] or [], w, matcher.can_continue)
        boxes = [tuple(int(v) for v in (m["x0"], m["y0"], m["x1"], m["y1"])) for m in lines]
        draw = ImageDraw.Draw(img)
        print(f"== {path.name}")
        for m, box in zip(lines, boxes):
            hit = matcher.lookup(m["text"])
            if not hit:
                draw.rectangle(box, outline=(255, 40, 40), width=2)
                print(f"  [없음] {m['text']}")
                continue
            size, ko_lines = layout(hit[2], box, int(m["line_h"]), *free_space(box, boxes, w, h), font_for, m.get("vertical", False))
            f = font_for(size)
            text = "\n".join(ko_lines)
            tb = draw.multiline_textbbox((box[0], box[1]), text, font=f.font, spacing=int(size * 0.33))
            edge = np.concatenate([arr[box[1], box[0]:box[2]], arr[box[3] - 1, box[0]:box[2]]])
            bg = tuple(int(v) for v in np.median(edge, axis=0))
            fg = (255, 255, 255) if sum(bg) / 3 < 140 else (17, 17, 17)
            draw.rectangle((min(box[0], tb[0]) - 3, min(box[1], tb[1]) - 3,
                            max(box[2], tb[2]) + 3, max(box[3], tb[3]) + 3), fill=bg)
            draw.multiline_text((box[0], box[1]), text, font=f.font, fill=fg, spacing=int(size * 0.33))
            print(f"  [{hit[0]}] {m['text']} → {' / '.join(ko_lines)} ({size}px)")
        out = path.with_name(path.stem + "_debug.png")
        img.save(out)
        print(f"  → {out.name}\n")


if __name__ == "__main__":
    main()
