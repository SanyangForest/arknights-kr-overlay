"""게임 화면 판정 (screens.json의 고정 문구로 지금 어느 화면인지 알아낸다)."""

import json
from pathlib import Path

import cv2

import paths

SCREENS = paths.CODE / "screens.json"


def load_screens():
    return json.loads(SCREENS.read_text(encoding="utf-8"))["screens"]


REF_W, REF_H = 1600, 900  # labels의 box 기준 크기


def _fit_axis(ref, cur):
    """cur ≈ a*ref + b. 기준 글자가 셋 이상이고 넓게 퍼져 있으면 비율도, 아니면 이동만."""
    n = len(ref)
    mr, mc = sum(ref) / n, sum(cur) / n
    var = sum((r - mr) ** 2 for r in ref)
    a = 1.0
    if n >= 3 and var > n * 150 ** 2:
        a = sum((r - mr) * (c - mc) for r, c in zip(ref, cur)) / var
        a = min(1.1, max(0.9, a))
    return a, mc - a * mr


_templates = {}
SEARCH = 90          # 예상 위치 주변 이 픽셀 안에서 원문 글자 그림을 찾음 (메뉴판이 마우스를 따라 80px쯤 움직임)
MATCH_MIN = 0.55     # 이보다 덜 닮았으면 글자가 안 보이는 것 (UI 숨김 등): 그리지 않음


def _template(path):
    if path not in _templates:
        _templates[path] = cv2.imread(str(paths.CODE / path), cv2.IMREAD_GRAYSCALE)
    return _templates[path]


def _locate(gray, box, path):
    """box 주변에서 원문 글자 그림(template)을 찾아 실제 자리. 못 찾으면 None."""
    tpl = _template(path)
    if gray is None or tpl is None:
        return box
    th, tw = tpl.shape
    x0, y0 = max(0, box[0] - SEARCH), max(0, box[1] - SEARCH)
    x1, y1 = min(gray.shape[1], box[0] + tw + SEARCH), min(gray.shape[0], box[1] + th + SEARCH)
    region = gray[y0:y1, x0:x1]
    if region.shape[0] < th or region.shape[1] < tw:
        return None
    res = cv2.matchTemplate(region, tpl, cv2.TM_CCOEFF_NORMED)
    _, score, _, (mx, my) = cv2.minMaxLoc(res)
    if score < MATCH_MIN:
        return None
    return (x0 + mx, y0 + my, x0 + mx + tw, y0 + my + th)


def labels_for(screen_id, screens, w, h, ocr=(), image=None):
    """그 화면의 고정 글자: [(box, zh, ko, vertical)]. box는 지금 게임 화면에 맞춰 옮긴 것.

    ocr: [(사각형, 글자, 점수)]. 화면 정의의 refs 글자가 보이면 그것들이 기준에서 움직인 만큼 함께 옮긴다
    (홈 메뉴판은 마우스를 따라 기울어 움직임). refs가 하나도 안 보이면 그리지 않는다.
    image: 게임 화면(RGB 배열). 주면 옮긴 자리 주변에서 원문 글자 그림을 찾아 정확히 맞추고, 못 찾으면 뺀다.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) if image is not None else None
    for s in screens:
        if s["id"] != screen_id or not s.get("labels"):
            continue
        sx, sy = w / REF_W, h / REF_H
        pairs = []
        for box, text, _ in ocr:
            ref = s.get("refs", {}).get(text.strip())
            if ref:
                cx = sum(p[0] for p in box) / len(box)
                cy = sum(p[1] for p in box) / len(box)
                pairs.append((ref[0] * sx, ref[1] * sy, cx, cy))
        if s.get("refs") and not pairs:
            return []
        ax, bx = _fit_axis([p[0] for p in pairs], [p[2] for p in pairs]) if pairs else (1.0, 0.0)
        ay, by = _fit_axis([p[1] for p in pairs], [p[3] for p in pairs]) if pairs else (1.0, 0.0)
        out = []
        for l in s["labels"]:
            x0, y0, x1, y1 = l["box"]
            box = (int(ax * x0 * sx + bx), int(ay * y0 * sy + by), int(ax * x1 * sx + bx), int(ay * y1 * sy + by))
            if l.get("template") and (w, h) == (REF_W, REF_H):  # 그림 찾기는 기준 크기 화면에서만
                box = _locate(gray, box, l["template"])
                if box is None:
                    continue
            out.append((box, l["zh"], l["ko"], l.get("vertical", False)))
        return out
    return []


def classify(texts, screens):
    """OCR 줄 목록으로 화면 id를 정한다. 위에 적힌 화면부터 고정 문구가 하나라도 있으면 그 화면, 없으면 'unknown'."""
    for s in screens:
        if any(a in t for a in s["anchors"] for t in texts):
            return s["id"]
    return "unknown"
