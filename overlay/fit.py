"""번역문을 원문 자리에 맞춰 배치한다 (다른 UI를 덮지 않도록).

우선순위:
  1. 원문 상자 폭·줄 수 안에서 글자 크기를 줄여 맞춘다.
  2. 안 되면 옆·아래의 다른 글자 영역에 닿기 직전까지만 넓힌다.
  3. 그래도 넘치면 마지막 줄을 '…'로 줄인다.
"""

import re

MIN_SCALE = 0.65   # 원래 글자 크기의 이 비율까지 줄임
MIN_SIZE = 10      # 픽셀
GAP = 6            # 다른 글자 영역과 띄울 간격
MAX_LINE_H = 120   # 이보다 큰 글자 줄은 이 높이로 보고 글자 크기를 정함 (큰 제목·이름용)
MAX_SIZE = 48      # 한글은 같은 픽셀 크기에서 한자보다 커 보이므로 큰 제목도 이 이상은 키우지 않음


def free_space(box, others, screen_w, screen_h):
    """box에서 오른쪽·아래로 다른 글자 영역에 닿기 전까지 쓸 수 있는 폭과 높이."""
    x0, y0, x1, y1 = box
    right, bottom = screen_w, screen_h
    for ox0, oy0, ox1, oy1 in others:
        if (ox0, oy0, ox1, oy1) == box:
            continue
        if oy0 < y1 and oy1 > y0 and ox0 >= x1 - 2:      # 같은 줄, 오른쪽
            right = min(right, ox0 - GAP)
        if ox0 < x1 and ox1 > x0 and oy0 >= y1 - 2:      # 같은 열, 아래쪽
            bottom = min(bottom, oy0 - GAP)
    return max(x1 - x0, right - x0), max(y1 - y0, bottom - y0)


def wrap(text, measure, width):
    """띄어쓰기 단위로 줄바꿈하고, 한 단어가 폭보다 길면 글자 단위로 자른다."""
    lines = []
    for para in text.split("\n"):
        cur = ""
        for word in re.findall(r"\S+\s*", para) or [""]:
            if measure(cur + word.rstrip()) <= width:
                cur += word
                continue
            if cur:
                lines.append(cur.rstrip())
                cur = ""
            while measure(word.rstrip()) > width and len(word) > 1:
                cut = len(word) - 1
                while cut > 1 and measure(word[:cut]) > width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            cur = word
        lines.append(cur.rstrip())
    return lines


def ellipsize(line, measure, width):
    while line and measure(line + "…") > width:
        line = line[:-1]
    return line + "…"


def layout(text, box, line_h, avail_w, avail_h, font_for, vertical=False):
    """(글자 크기, 줄 목록)을 고른다. font_for(size)는 measure 가능한 폰트를 돌려준다.

    vertical: 원문이 세로쓰기(홈의 '干/员' 같은 큰 메뉴 글자). 한국어는 가로로 눕혀
    원문 한 글자 크기로 쓰고, 오른쪽 빈 공간까지 펼친다 (좁은 폭에 세로로 쌓으면 '오퍼/레이/터'처럼 됨).
    """
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    base = max(MIN_SIZE, min(MAX_SIZE, int(min(line_h, MAX_LINE_H) * 0.72)))
    if vertical:
        sizes = list(range(base, max(MIN_SIZE, int(base * 0.6)) - 1, -1))
        width = min(avail_w, max(bw * 5, 240))
        # 1. 가로 한 줄 (원래 크기에 가깝게)
        for size in sizes[:max(1, len(sizes) // 2)]:
            if font_for(size).measure(text) <= width:
                return size, [text]
        # 2. 짧으면 원문처럼 한 글자씩 세로로 (좁은 칸의 '단/말/기')
        chars = text.replace(" ", "")
        if len(chars) <= 4:
            for size in sizes:
                f = font_for(size)
                if f.measure("가") <= bw * 1.1 and len(chars) * f.metrics("linespace") <= bh * 1.05:
                    return size, list(chars)
        # 3. 가로 두 줄까지
        for size in sizes:
            f = font_for(size)
            lines = wrap(text, f.measure, width)
            if len(lines) <= 2 and len(lines) * f.metrics("linespace") <= max(bh, avail_h):
                return size, lines
        return sizes[-1], wrap(text, font_for(sizes[-1]).measure, width)[:2]
    n_lines = max(1, round(bh / max(line_h, 1)))
    # 한 줄짜리 짧은 라벨(버튼 등)은 옆으로 넓히면 버튼 밖으로 삐져나오므로 더 줄여 보고, 넓혀도 1.6배까지만
    label = n_lines == 1 and bw < 400
    # 짧은 라벨은 한 단어가 글자 단위로 끊기지 않게('먼슬리카/드') 더 작게까지 줄여 본다
    min_scale = (0.4 if " " not in text else 0.5) if label else MIN_SCALE
    sizes = list(range(base, max(MIN_SIZE, int(base * min_scale)) - 1, -1))
    if label:
        avail_w = min(avail_w, bw * 1.6)

    def lines_fit(size, width, height):
        f = font_for(size)
        lines = wrap(text, f.measure, width)
        return lines if len(lines) * f.metrics("linespace") <= height else None

    # 1. 원래 자리 안 (폭은 약간 여유, 높이는 원래 줄 수만큼)
    for size in sizes:
        lines = lines_fit(size, bw * 1.1, max(bh, n_lines * font_for(size).metrics("linespace")) * 1.05)
        if lines:
            return size, lines
    # 2. 빈 공간까지 넓히기
    for size in sizes:
        lines = lines_fit(size, avail_w, avail_h)
        if lines:
            return size, lines
    # 3. 최소 크기에서 들어가는 줄까지만 쓰고 말줄임
    size = sizes[-1]
    f = font_for(size)
    lines = wrap(text, f.measure, avail_w)
    max_lines = max(1, int(avail_h // f.metrics("linespace")))
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = ellipsize(lines[-1], f.measure, avail_w)
    return size, lines
