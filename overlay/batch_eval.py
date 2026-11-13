"""기록된 화면 전체에 대해 OCR → 매칭 → 배치를 돌려 통계와 점검용 이미지를 만든다.

OCR 결과는 frame_XXXX.ocr.json으로 캐시하므로, 매칭·배치 규칙을 고친 뒤 다시 돌리면 빠르다.

출력 (<기록 폴더>_eval/):
  report.md        방식별 개수, 번역 못 한 문구 빈도, 비슷한 문장 매칭 목록, 말줄임·작은 글씨 목록
  render/*.png     번역을 덮어 그린 화면 (빨간 테두리 = 번역 못 함)

사용법: python batch_eval.py <사전 json> <기록 폴더> [--render N]
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from debug_render import PilFont
from fit import MIN_SCALE, free_space, layout
from layout import merge_lines
from matcher import HAN, Matcher, edge_of
from screens import classify, load_screens


def ocr_cached(ocr_fn, frame):
    cache = frame.with_suffix(".ocr.json")
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    result = ocr_fn()(np.asarray(Image.open(frame).convert("RGB")))[0] or []
    data = [[[[float(v) for v in p] for p in box], text, float(score)] for box, text, score in result]
    cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("dict_path")
    ap.add_argument("folder", type=Path)
    ap.add_argument("--render", type=int, default=0, help="점검용 이미지를 N장마다 1장 그림 (0=안 그림)")
    args = ap.parse_args()

    matcher = Matcher(args.dict_path)
    screens = load_screens()
    ocr = None

    def ocr_fn():
        nonlocal ocr
        if ocr is None:
            from ocr_engine import make_ocr
            ocr = make_ocr(gpu=True)
        return ocr

    fonts = {}

    def font_for(size):
        if size not in fonts:
            fonts[size] = PilFont(size)
        return fonts[size]

    out = args.folder.with_name(args.folder.name + "_eval")
    (out / "render").mkdir(parents=True, exist_ok=True)
    how_count = Counter()
    missing = Counter()
    missing_where = {}
    fuzzy = Counter()
    segment = Counter()
    truncated = Counter()
    small = Counter()

    frames = sorted(args.folder.glob("frame_*.png"))
    for idx, frame in enumerate(frames):
        img = Image.open(frame).convert("RGB")
        w, h = img.size
        raw = ocr_cached(ocr_fn, frame)
        screen = classify([t for _, t, _ in raw], screens)
        lines = merge_lines(raw, w, matcher.can_continue, matcher.is_entry, lambda t: matcher.lookup(t, screen) is not None)
        boxes = [tuple(int(v) for v in (m["x0"], m["y0"], m["x1"], m["y1"])) for m in lines]
        render = args.render and idx % args.render == 0
        draw = ImageDraw.Draw(img) if render else None
        arr = np.asarray(img) if render else None
        for m, box in zip(lines, boxes):
            if not HAN.search(m["text"]) or matcher.is_noise(m["text"]):
                continue
            hit = matcher.lookup(m["text"], screen, edge_of(box, w))
            if not hit:
                how_count["없음"] += 1
                missing[m["text"]] += 1
                missing_where.setdefault(m["text"], frame.stem)
                if render:
                    draw.rectangle(box, outline=(255, 40, 40), width=2)
                continue
            how, zh, ko, score = hit
            how_count[how] += 1
            if how == "fuzzy":
                fuzzy[(m["text"], zh, ko, score)] += 1
            elif how == "segment":
                segment[(m["text"], ko)] += 1
            size, ko_lines = layout(ko, box, int(m["line_h"]), *free_space(box, boxes, w, h), font_for,
                                    m.get("vertical", False))
            base = max(10, int(min(m["line_h"], 48) * 0.72))
            if ko_lines and ko_lines[-1].endswith("…") and not ko.endswith("…"):
                truncated[(m["text"], ko)] += 1
            elif size <= max(10, int(base * MIN_SCALE)) and size < base:
                small[(m["text"], ko, size)] += 1
            if render:
                f = font_for(size)
                text = "\n".join(ko_lines)
                spacing = int(size * 0.33)
                tb = draw.multiline_textbbox((box[0], box[1]), text, font=f.font, spacing=spacing)
                edge = np.concatenate([arr[box[1], box[0]:box[2]], arr[min(box[3], h - 1), box[0]:box[2]]])
                bg = tuple(int(v) for v in np.median(edge, axis=0))
                fg = (255, 255, 255) if sum(bg) / 3 < 140 else (17, 17, 17)
                draw.rectangle((min(box[0], tb[0]) - 3, min(box[1], tb[1]) - 3,
                                max(box[2], tb[2]) + 3, max(box[3], tb[3]) + 3), fill=bg)
                draw.multiline_text((box[0], box[1]), text, font=f.font, fill=fg, spacing=spacing)
        if render:
            img.save(out / "render" / frame.name)
        print(f"\r{idx + 1}/{len(frames)}", end="", flush=True)
    print()

    total = sum(how_count.values())
    rep = [f"# 기록 점검: {args.folder.name} ({len(frames)}장)", "",
           "## 한자가 있는 글자 영역", "", "| 방식 | 개수 | 비율 |", "|---|---:|---:|"]
    for k, v in how_count.most_common():
        rep.append(f"| {k} | {v:,} | {v / total:.1%} |")
    rep += ["", f"## 번역 못 한 문구 (고유 {len(missing):,}개, 많이 나온 순)", "", "| 횟수 | 문구 | 처음 나온 화면 |", "|---:|---|---|"]
    rep += [f"| {n} | {t} | {missing_where[t]} |" for t, n in missing.most_common(150)]
    rep += ["", f"## 비슷한 문장 매칭 (고유 {len(fuzzy):,}개) — 오역 확인용", "", "| 점수 | OCR | 사전 원문 | 번역 |", "|---:|---|---|---|"]
    rep += [f"| {s} | {o} | {z} | {k} |" for (o, z, k, s), _ in sorted(fuzzy.items(), key=lambda x: x[0][3])[:120]]
    rep += ["", f"## 부분 번역 (고유 {len(segment):,}개)", "", "| OCR | 번역 |", "|---|---|"]
    rep += [f"| {o} | {k} |" for (o, k), _ in segment.most_common(80)]
    rep += ["", f"## 말줄임된 번역 (고유 {len(truncated):,}개)", "", "| OCR | 전체 번역 |", "|---|---|"]
    rep += [f"| {o} | {k} |" for (o, k), _ in truncated.most_common(60)]
    rep += ["", f"## 최소 크기까지 줄어든 번역 (고유 {len(small):,}개)", "", "| OCR | 번역 | 크기 |", "|---|---|---:|"]
    rep += [f"| {o} | {k} | {s}px |" for (o, k, s), _ in small.most_common(60)]
    (out / "report.md").write_text("\n".join(rep) + "\n", encoding="utf-8")
    with open(out / "missing.tsv", "w", encoding="utf-8", newline="") as f:
        f.write("count\ttext\tframe\n")
        for t, n in missing.most_common():
            f.write(f"{n}\t{t.replace(chr(9), ' ')}\t{missing_where[t]}\n")
    print("\n".join(rep[:12]))
    print(f"→ {out / 'report.md'}")


if __name__ == "__main__":
    main()
