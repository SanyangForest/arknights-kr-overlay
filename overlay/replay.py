"""녹화한 화면들을 오버레이와 같은 순서로 처리해 번역을 덮어 그린 그림을 만든다 (게임 없이 점검).

Worker.analyze(OCR → 스토리 따라가기 → 사전 매칭)와 render.item_layer(상자 그리기)를 그대로 쓰므로
실제 오버레이가 그 화면에서 그릴 것과 같다. 스토리 따라가기처럼 앞 화면을 기억하는 기능도 순서대로 넣으면 확인된다.

사용법: python replay.py captures/session1 [--from 212] [--to 229] [--out captures/session1_replay]
"""

import argparse
import queue
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

import render
from matcher import Matcher
from overlay import Worker

ROOT = Path(__file__).resolve().parent


def draw(img, items):
    arr = np.asarray(img)
    for it in items:
        render.attach_patch(it, arr)
    order = sorted(items, key=lambda it: (it["how"] in render.PANELS,
                                          -(it["box"][2] - it["box"][0]) * (it["box"][3] - it["box"][1])))
    layers = [(z, im, pos) for it in order for im, pos, z in render.item_layers(it)]
    layers = [(im, pos, 1.0) for z, im, pos in sorted(layers, key=lambda l: l[0])]
    out = img.convert("RGBA")
    out.alpha_composite(render.compose(img.size, layers))
    return out.convert("RGB")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--from", dest="start", type=int, default=1)
    ap.add_argument("--to", type=int, default=99999)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--dict", default=str(ROOT.parent / "out" / "dict_zh_ko.json"))
    ap.add_argument("--style", choices=("natural", "box"), default="natural", help="그리는 방식 (render.STYLE)")
    a = ap.parse_args()
    render.STYLE = a.style
    out = a.out or a.folder.with_name(a.folder.name + "_replay")
    out.mkdir(parents=True, exist_ok=True)
    worker = Worker(None, Matcher(a.dict), queue.Queue(), offline=True)
    files = [f for f in sorted(a.folder.glob("frame_*.png")) if a.start <= int(f.stem[6:]) <= a.to]
    for f in files:
        img = Image.open(f).convert("RGB")
        t = time.perf_counter()
        items, _ = worker.analyze(np.asarray(img), *img.size)
        ms = (time.perf_counter() - t) * 1000
        draw(img, items).save(out / f.name)
        story = [it for it in items if it["how"] == "story"]
        print(f"{f.name} {ms:4.0f}ms 상자 {len(items)} (스토리 {len(story)})"
              + "".join(f"\n    {it['zh'][:24]} → {it['ko'][:40]}" for it in story), flush=True)


if __name__ == "__main__":
    main()
