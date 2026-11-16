"""창고 인식: 창고 화면의 아이템 칸을 찾아 무슨 아이템인지(MAA 아이콘 템플릿과 비교)와 수량(숫자 OCR)을 읽는다.

  1. 아이템 칸: 동그란 테두리를 원 검출(HoughCircles)로 찾는다 (8열 격자).
  2. 아이템: 칸 가운데를 잘라 MAA 아이템 템플릿 497개와 비교 (원 안쪽만, 수량 칸은 빼고). 크기는 1.15~1.25배 중 가장 잘 맞는 것.
  3. 수량: 칸 오른쪽 아래 숫자 칸만 잘라 글자 읽기 모델로 읽는다 ('46.6万' → 466000). 일반 OCR은 한 자리 숫자를 자주 놓침.
결과는 overlay/data/inventory.json = {"updated": "...", "items": {아이템 id: 수량}}.
템플릿: tools/maa_templates/resource/template/items (MAA 저장소, 이 컴퓨터에서만 씀).
"""

import json
import re
import time
from pathlib import Path

import cv2
import numpy as np

import paths

TEMPLATES = paths.MAA_ITEMS  # 없으면 창고 인식만 꺼짐 (배포판에는 넣지 않음)
INVENTORY = paths.USER_DATA / "inventory.json"
SIZE = 64            # 비교용으로 줄인 크기
MIN_SCORE = 0.6      # 이보다 덜 닮으면 모르는 아이템 (기간 한정 아이템 등 템플릿 없음)
SCALES = (1.15, 1.2, 1.25)  # 템플릿(720p 기준 105px) 대비 칸 크기 배율 (화면 높이/720 기준)
COUNT = re.compile(r"(\d+(?:\.\d+)?)(万)?")


def _mask():
    yy, xx = np.mgrid[:SIZE, :SIZE]
    inside = (xx - SIZE / 2) ** 2 + (yy - SIZE / 2) ** 2 < (SIZE * 0.42) ** 2
    count_box = (xx > SIZE * 0.55) & (yy > SIZE * 0.62)
    return inside & ~count_box


MASK = _mask()


def _vec(img):
    v = cv2.resize(img, (SIZE, SIZE), interpolation=cv2.INTER_AREA).astype(np.float32)[MASK]
    v -= v.mean(0)
    return (v / (np.linalg.norm(v) + 1e-6)).ravel()


def parse_count(text):
    m = COUNT.search(text.replace(",", ""))
    if not m:
        return None
    n = float(m.group(1)) * (10000 if m.group(2) else 1)
    return int(round(n))


class Depot:
    def __init__(self):
        files = sorted(TEMPLATES.glob("*.png"))
        self.ids = [f.stem for f in files]
        self.T = np.stack([_vec(cv2.imread(str(f))) for f in files]) if files else None

    def cells(self, bgr):
        """아이템 칸 [(cx, cy, r)] 위→아래, 왼→오른."""
        g = cv2.medianBlur(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), 5)
        h = g.shape[0]
        c = cv2.HoughCircles(g, cv2.HOUGH_GRADIENT, dp=1.2, minDist=int(h * 0.15), param1=120, param2=40,
                             minRadius=int(h * 0.07), maxRadius=int(h * 0.11))
        if c is None:
            return []
        return sorted((tuple(int(v) for v in x) for x in c[0]), key=lambda t: (t[1] // int(h * 0.05), t[0]))

    def identify(self, bgr, cx, cy):
        """(아이템 id, 닮은 정도) — 모르면 (None, 점수)."""
        h = bgr.shape[0]
        best = (None, -1.0)
        for s in SCALES:
            half = int(105 * h / 720 * s / 2)
            crop = bgr[max(0, cy - half):cy + half, max(0, cx - half):cx + half]
            if crop.shape[0] < 20 or crop.shape[1] < 20:
                continue
            scores = self.T @ _vec(crop)
            i = int(scores.argmax())
            if scores[i] > best[1]:
                best = (self.ids[i], float(scores[i]))
        return best if best[1] >= MIN_SCORE else (None, best[1])

    @staticmethod
    def count_crop(rgb, cx, cy, r):
        """수량 숫자만 딱 맞게 자른 그림 (칸 오른쪽 아래의 흰 글자).

        넓게 자르면 한 자리 숫자를 글자 읽기 모델이 놓치고, 좁게 자르면 '46.6万'의 앞자리가 잘린다.
        그래서 넓은 영역에서 흰 글자 덩어리(숫자 높이)를 찾아, 오른쪽 끝에 붙은 덩어리들만 묶어 자른다.
        """
        x0, y0 = max(0, int(cx - 0.6 * r)), max(0, int(cy + 0.2 * r))
        x1, y1 = int(cx + 1.15 * r), int(cy + 0.85 * r)
        region = rgb[y0:y1, x0:x1]
        if region.size == 0:
            return region
        white = (cv2.cvtColor(region, cv2.COLOR_RGB2GRAY) > 190).astype(np.uint8)
        n, _lab, stats, _ = cv2.connectedComponentsWithStats(white)
        parts = [s for s in stats[1:] if 0.1 * r <= s[3] <= 0.4 * r and s[2] <= 0.4 * r]
        if not parts:
            return region
        right = max(s[0] + s[2] for s in parts)
        # 오른쪽 끝 덩어리와 같은 줄에 있고, 서로 글자 하나 간격 안으로 이어진 덩어리들
        parts.sort(key=lambda s: -(s[0] + s[2]))
        row = [parts[0]]
        for s in parts[1:]:
            lead = min(p[0] for p in row)
            same_line = abs((s[1] + s[3] / 2) - (row[0][1] + row[0][3] / 2)) < 0.12 * r
            if same_line and lead - (s[0] + s[2]) < 0.25 * r:
                row.append(s)
        bx0 = min(s[0] for s in row)
        by0 = min(s[1] for s in row)
        by1 = max(s[1] + s[3] for s in row)
        pad = max(3, int(0.06 * r))
        crop = region[max(0, by0 - pad):by1 + pad, max(0, bx0 - pad):right + pad]
        return cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)

    @staticmethod
    def count_by_det(rgb, cx, cy, r, ocr):
        """수량 칸 영역을 2배로 키워 글자 위치 찾기+읽기를 하고, 숫자가 든 가장 오른쪽 글. 두 자리 이상은 이게 정확함."""
        region = rgb[max(0, int(cy + 0.15 * r)):int(cy + 0.9 * r), max(0, int(cx - 0.6 * r)):int(cx + 1.15 * r)]
        if region.size == 0:
            return None
        big = cv2.resize(region, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        res, _ = ocr(cv2.copyMakeBorder(big, 20, 20, 20, 20, cv2.BORDER_REPLICATE))
        cands = [(max(p[0] for p in b), t) for b, t, _s in (res or []) if re.search(r"\d", t)]
        return parse_count(max(cands)[1]) if cands else None

    def read(self, rgb, ocr):
        """한 화면의 [(아이템 id, 수량)]. ocr: RapidOCR (make_ocr) — 숫자 칸 읽기에 씀.

        두 자리 이상은 글자 위치 찾기로, 거기서 못 찾는 한 자리 숫자는 흰 글자만 잘라 읽는다.
        """
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        out = []
        for cx, cy, r in self.cells(bgr):
            iid, _score = self.identify(bgr, cx, cy)
            if not iid:
                continue
            n = self.count_by_det(rgb, cx, cy, r, ocr)
            if n is None:
                crop = self.count_crop(rgb, cx, cy, r)
                if crop.size:
                    n = parse_count(ocr.text_recognizer([crop])[0][0][0])
            if n is not None:
                out.append((iid, n))
        return out


def load():
    try:
        return json.loads(INVENTORY.read_text(encoding="utf-8"))["items"]
    except (OSError, ValueError, KeyError):
        return {}


def save(items):
    INVENTORY.parent.mkdir(parents=True, exist_ok=True)
    INVENTORY.write_text(json.dumps({"updated": time.strftime("%Y-%m-%d %H:%M"), "items": items},
                                    ensure_ascii=False, indent=1), encoding="utf-8")
