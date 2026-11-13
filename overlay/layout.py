"""OCR 결과(줄 단위)를 화면에 표시할 단위로 정리한다."""

import re

HAN = re.compile(r"[一-鿿]")

# 이 폭 이상인 줄 바로 아래 줄은 자동 줄바꿈된 같은 문장으로 본다 (대사창처럼 넓은 영역)
WRAP_MIN_RATIO = 0.3


def to_box(item):
    """OCR 사각형 → 감싸는 상자 + 글줄 높이(lh).

    긴 줄은 OCR 사각형이 살짝 기울어 잡혀 감싸는 상자가 실제 글자보다 훨씬 높아지므로,
    글자 크기를 정할 높이는 사각형 왼쪽·오른쪽 변 길이의 평균으로 잰다.
    세로로 쓴 짧은 글자(홈의 '干/员')는 상자 하나에 글자가 위아래로 놓이므로 높이를 글자 수로 나눈다.
    """
    box, text, score = item
    xs, ys = [p[0] for p in box], [p[1] for p in box]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    lh = (abs(box[3][1] - box[0][1]) + abs(box[2][1] - box[1][1])) / 2 if len(box) == 4 else y1 - y0
    n = len(text.strip())
    # 가로 두 글자는 높이가 폭의 절반쯤, 세로 두 글자는 두 배쯤이므로 1.3배로 가른다
    vertical = 2 <= n <= 6 and (y1 - y0) > 1.3 * (x1 - x0) and (y1 - y0) / n < (x1 - x0) * 1.5
    if vertical:
        lh = (y1 - y0) / n
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1, "text": text, "score": score,
            "lh": max(1.0, min(lh, y1 - y0)), "vertical": vertical}


def _join_same_line(boxes, can_continue):
    """OCR이 한 줄을 둘로 쪼갠 경우 (예: '新朋友、' + '老对头……') 이어 붙인다.

    같은 줄의 별개 UI 문구(예: '攻击564'와 '防御88')를 붙이지 않도록,
    이은 문장이 사전의 앞부분일 때만 붙인다.
    """
    if not can_continue:
        return boxes
    boxes = sorted(boxes, key=lambda b: (b["x0"], b["y0"]))
    out = []
    for b in boxes:
        h = b["y1"] - b["y0"]
        for m in out:
            # 숫자만 있는 조각(알림 개수 '39')을 옆 문구('5天')에 붙이면 '395天'처럼 숫자가 바뀜
            if not HAN.search(m["text"]) or m.get("vertical") or b.get("vertical"):
                continue
            overlap = min(m["y1"], b["y1"]) - max(m["y0"], b["y0"])
            gap = b["x0"] - m["x1"]
            # OCR 상자는 옆 상자와 글자 반 개 정도 겹치기도 함
            if overlap > 0.5 * min(h, m["y1"] - m["y0"]) and -h * 0.6 <= gap < h * 2 \
                    and can_continue(m["text"] + b["text"]):
                m["text"] += b["text"]
                m["x1"], m["y0"], m["y1"] = b["x1"], min(m["y0"], b["y0"]), max(m["y1"], b["y1"])
                m["lh"] = max(m["lh"], b["lh"])
                break
        else:
            out.append(dict(b))
    return out


def merge_lines(items, screen_w, can_continue=None, is_entry=None, known=None):
    """줄바꿈된 같은 문장을 하나로 합친다.

    합치는 조건: 바로 아래 줄이고 (OCR 상자가 살짝 겹치는 것도 허용), 왼쪽 정렬과 글자 높이가 비슷하며,
      - 윗줄이 넓거나 (대사창 등), 또는
      - 두 줄을 이은 문장이 사전의 어떤 문장의 앞부분일 때 (좁은 설명 패널)
    메뉴 목록처럼 이어 붙여도 사전에 없는 짧은 줄들은 따로 둔다.
    가운데 정렬 말풍선의 짧은 끝줄('吗？')은 윗줄 폭 안에 있고 이은 문장이 사전에 있으면 합친다.
    is_entry가 있으면, 각각 사전에 그대로 있는 두 줄은 이은 문장이 사전에 없는 한 넓어도 합치지 않는다
    (배너의 '寻访现已开放' / '特定干员出率上升').
    known이 있으면, 합친 문장을 사전에서 못 찾을 때 원래 줄들로 되돌린다
    (스탯 표의 '生命上限 1888' 아래 '攻击'처럼 이어 붙이면 둘 다 번역을 못 하는 경우).
    """
    boxes = _join_same_line([to_box(i) for i in items], can_continue)
    boxes.sort(key=lambda b: (b["y0"], b["x0"]))
    merged = []
    for b in boxes:
        lh = b["lh"]
        cy = (b["y0"] + b["y1"]) / 2
        for m in merged:
            # 세로쓰기 글자(홈의 '干/员')는 그 자체로 완결: 아래 머리글('招募')과 이어 '干员招募'로 만들지 않음.
            # 한자가 없는 아랫줄(배너 아래 개수 'x1')도 잇지 않음
            if m.get("vertical") or b.get("vertical") or not HAN.search(b["text"]):
                continue
            last_w = m["last_x1"] - m["x0"]
            last_lh = m["last_lh"]
            ratio = lh / max(last_lh, 1)
            # 줄 사이는 상자 끝이 아니라 줄 가운데 간격과 글줄 높이로 판단한다.
            # 긴 줄은 OCR 상자가 기울어 실제 글자보다 두 배 높게 잡혀 위아래 상자가 크게 겹치기 때문 (말풍선)
            dy = cy - m["last_cy"]
            unit = min(lh, last_lh)
            below = 0.6 * unit <= dy <= 2.2 * max(lh, last_lh)
            if not below:
                continue
            left_aligned = abs(m["x0"] - b["x0"]) < unit * 0.8
            # 짧은 끝줄은 글자 높이가 비슷하고, 이은 문장이 사전 문장의 '정확한' 앞부분일 때만
            # (비슷한 문장까지 허용하면 말풍선 옆 '好友'·배너 '月卡'까지 꼬리로 붙음)
            short_tail = (len(b["text"]) <= 3 and m["x0"] <= (b["x0"] + b["x1"]) / 2 <= m["x1"]
                          and 0.6 < ratio < 1.6 and can_continue and can_continue(m["text"] + b["text"], False))
            joined_known = can_continue and can_continue(m["text"] + b["text"])
            # 각각 사전에 그대로 있는 두 줄(대사 기록의 화자별 대사 '两点之间，直线最短。' / '没错。')은
            # 이은 문장이 사전 문장의 '정확한' 앞부분일 때만 잇는다 (비슷한 문장이 있다고 잇지 않음)
            if joined_known and is_entry and is_entry(m["text"]) and is_entry(b["text"])                     and not can_continue(m["text"] + b["text"], False):
                joined_known = False
            # 가운데 정렬된 여러 줄(통합 전략 이벤트 설명, 오퍼레이터 대사): 윗줄과 가운데가 같고 이은 문장이 사전에 있으면 합침
            centered = (abs(m["last_cx"] - (b["x0"] + b["x1"]) / 2) < unit * 2 and 0.7 < ratio < 1.45
                        and joined_known)
            if not (left_aligned or short_tail or centered):
                continue
            # 짧은 마지막 줄은 OCR 상자가 크게 잡히기도 하므로, 사전 근거가 있으면 높이 차이를 더 허용
            wide = last_w >= screen_w * WRAP_MIN_RATIO and 0.7 < ratio < 1.45
            # 넓은 줄 아래라도, 아랫줄이 사전에 그대로 있는 별개 문구이고 이은 문장은 사전에 없으면 합치지 않음
            # (말풍선 아래의 배너 '博览群书组合包', 배너 두 줄 '寻访现已开放' / '特定干员出率上升')
            if wide and is_entry and is_entry(b["text"]) and not joined_known:
                wide = False
            if wide or short_tail or centered or (0.6 < ratio < 1.7 and joined_known):
                m["text"] += b["text"]
                m["x0"], m["x1"], m["y1"] = min(m["x0"], b["x0"]), max(m["x1"], b["x1"]), b["y1"]
                m["last_y0"], m["last_x1"], m["last_cy"], m["last_lh"] = b["y0"], b["x1"], cy, lh
                m["last_cx"] = (b["x0"] + b["x1"]) / 2
                # 글줄 높이는 여러 줄의 가운데값: OCR이 가끔 두 줄을 한 상자로 잡아 한 줄만 두 배로 나옴
                m["lhs"].append(lh)
                m["line_h"] = sorted(m["lhs"])[len(m["lhs"]) // 2]
                m["n_lines"] += 1
                m["vertical"] = False
                m["parts"].append(b)
                break
        else:
            merged.append({**b, "last_y0": b["y0"], "last_x1": b["x1"], "last_cy": cy, "last_lh": lh,
                           "last_cx": (b["x0"] + b["x1"]) / 2, "line_h": lh, "lhs": [lh], "n_lines": 1, "parts": [b]})
    if known:
        out = []
        for m in merged:
            if m["n_lines"] > 1 and not known(m["text"]):
                out.extend({**b, "line_h": b["lh"], "n_lines": 1} for b in m["parts"])
            else:
                out.append(m)
        merged = out
    return merged
