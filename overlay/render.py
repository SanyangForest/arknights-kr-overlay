"""번역 상자를 픽셀 단위 투명도로 그린다 (레이어드 창 + UpdateLayeredWindow).

Tk의 투명색(-transparentcolor) 방식은 글자 가장자리를 투명한 바탕과 섞을 수 없어 계단처럼 깨지고 색 테두리가 남는다.
PIL로 창 전체 크기의 RGBA 그림을 그려 한 번에 올리면 부드러운 글자, 둥근 상자, 서서히 나타나기를 쓸 수 있다.
창 자체(클릭 통과, 캡처 제외)는 overlay.py가 만든 Tk 창을 그대로 쓴다.
"""

import ctypes
from ctypes import wintypes

from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

import paths
from fit import layout

FONT_PATH = "C:/Windows/Fonts/malgun.ttf"
BOLD_PATH = "C:/Windows/Fonts/malgunbd.ttf"
# 자연스럽게 그리기(natural)의 글꼴: 게임 중국어 글꼴(본고딕 SC)과 같은 집안인 본고딕 KR(Noto Sans KR).
# 함께 넣은 글꼴(overlay/fonts)이 먼저, 없으면 Windows에 깔린 것, 그것도 없으면 맑은 고딕
NOTO_PATHS = [paths.CODE / "fonts" / "NotoSansKR-VF.ttf", Path("C:/Windows/Fonts/NotoSansKR-VF.ttf")]
NOTO_PATH = next((str(p) for p in NOTO_PATHS if p.exists()), None)
# 그리는 방식: "natural" = 원문 글자만 지우고(주변 배경으로 메움) 그 자리에 한국어를 씀 (한국어판처럼),
# "box" = 배경색 상자로 덮고 한국어를 씀 (예전 방식). overlay.py가 settings.json의 render_style로 정한다
STYLE = "natural"
PAD = 3
RADIUS = 5
# 등급 색 (공개모집 패널)
RARITY_COLOR = {6: (255, 127, 39), 5: (255, 208, 64), 4: (205, 170, 255), 3: (170, 170, 170), 1: (190, 220, 255)}

# 함수 모양(argtypes)을 따로 정하므로 ctypes.windll 공용 객체가 아닌 별도 객체를 쓴다.
# 공용 객체를 바꾸면 overlay.grab_window처럼 기본 모양을 믿는 다른 코드가 깨진다 (GetDC가 64비트 핸들을 돌려줌)
PANELS = ("recruit", "panel")  # 원문을 덮지 않고 옆에 띄우는 정보 패널 (공개모집 조합, 스테이지 드롭 등)

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32")


class PilFont:
    """fit.layout이 쓰는 Tk 글꼴과 같은 모양(measure, metrics('linespace'))의 PIL 글꼴."""

    def __init__(self, size, bold=False, natural=False):
        if natural and NOTO_PATH:
            self.font = ImageFont.truetype(NOTO_PATH, size)
            self.font.set_variation_by_axes([700 if bold else 500])
        else:
            self.font = ImageFont.truetype(BOLD_PATH if bold else FONT_PATH, size)
        ascent, descent = self.font.getmetrics()
        self.linespace = ascent + descent

    def measure(self, text):
        return int(self.font.getlength(text))

    def metrics(self, what):
        return self.linespace


_fonts = {}


def font(size, bold=False, natural=False):
    key = (size, bold, natural)
    if key not in _fonts:
        _fonts[key] = PilFont(size, bold, natural)
    return _fonts[key]


def natural_font(size):
    return font(size, natural=True)


def lum(c):
    return (c[0] * 299 + c[1] * 587 + c[2] * 114) / 1000


def fill_from_around(img, mask, line_h):
    """mask 자리를 둘레 픽셀의 가중 평균으로 메운다 (정규화한 흐림: 가려진 픽셀은 빼고 평균).

    cv2.inpaint는 글이 많은 화면에서 항목 20개에 0.9초 걸려 못 씀. 상자 흐림은 크기와 상관없이 빨라 1ms 안팎.
    좁은 창(글자 높이)으로 먼저 메우고, 그 안에 둘레 픽셀이 거의 없는 자리는 넓은 창으로.
    """
    keep = (mask == 0).astype(np.float32)
    src = img.astype(np.float32) * keep[..., None]
    out = img.astype(np.float32)
    todo = mask > 0
    for k in (max(5, int(line_h) | 1), max(15, int(line_h * 3) | 1)):
        den = cv2.blur(keep, (k, k))
        num = cv2.blur(src, (k, k))
        ok = todo & (den > 0.05)
        out[ok] = num[ok] / den[ok][:, None]
        todo &= ~ok
        if not todo.any():
            break
    if todo.any():
        out[todo] = np.median(img[mask == 0], axis=0) if (mask == 0).any() else 0
    return np.clip(out, 0, 255).astype(np.uint8)


def make_patch(arr, box, bg, line_h):
    """원문 글자를 지운 배경 조각: (RGBA 그림, 상자 왼쪽 위에서의 위치). 자연스럽게 그리기에서 원문 자리에 덮는다.

    상자 안에서 배경색(bg)과 뚜렷이 다른 픽셀(글자와 그 테두리)을 조금 넓혀 표시하고, 주변 배경으로 메운다
    (fill_from_around). 버튼 무늬·그러데이션이 그대로 남아 상자를 덮은 티가 나지 않는다. 가장자리는 서서히 투명하게.
    """
    x0, y0, x1, y1 = (int(v) for v in box)
    H, W = arr.shape[:2]
    m = max(2, int(line_h * 0.12))
    cx0, cy0, cx1, cy1 = max(0, x0 - m * 2), max(0, y0 - m * 2), min(W, x1 + m * 2), min(H, y1 + m * 2)
    if cx1 - cx0 < 4 or cy1 - cy0 < 4:
        return None
    crop = np.ascontiguousarray(arr[cy0:cy1, cx0:cx1, :3])
    diff = np.abs(crop.astype(np.int16) - np.array(bg, dtype=np.int16)).max(axis=2)
    mask = np.zeros(diff.shape, np.uint8)
    ix0, iy0 = max(0, x0 - m - cx0), max(0, y0 - m - cy0)
    ix1, iy1 = min(cx1, x1 + m) - cx0, min(cy1, y1 + m) - cy0
    mask[iy0:iy1, ix0:ix1] = (diff[iy0:iy1, ix0:ix1] > 48).astype(np.uint8) * 255
    k = max(3, m * 2 + 1)
    mask = cv2.dilate(mask, np.ones((k, k), np.uint8))
    filled = fill_from_around(crop, mask, line_h) if mask.any() else crop
    # 덮을 범위: 상자 + m, 그 바깥 m은 서서히 투명하게
    alpha = np.zeros(diff.shape, np.float32)
    alpha[iy0:iy1, ix0:ix1] = 1.0
    alpha = cv2.blur(alpha, (m * 2 + 1, m * 2 + 1))
    alpha = np.clip(alpha * 2.0, 0, 1)
    rgba = np.dstack([filled, (alpha * 255).astype(np.uint8)])
    return Image.fromarray(rgba, "RGBA"), (cx0 - x0, cy0 - y0)


def text_color(bg, fg=None):
    """원문 글자색(fg)이 배경과 충분히 다르면 그 색, 아니면 배경 밝기에 맞춰 흰색/검은색."""
    lum = lambda c: (c[0] * 299 + c[1] * 587 + c[2] * 114) / 1000  # noqa: E731
    if fg is not None and abs(lum(fg) - lum(bg)) >= 90:
        return tuple(fg)
    return (255, 255, 255) if lum(bg) < 140 else (17, 17, 17)


def item_layer(it):
    """번역 상자 하나를 그린 (그림, 왼쪽 위 좌표, 덮은 자리). 덮은 자리는 오버레이 창 좌표."""
    x0, y0, x1, y1 = it["box"]
    bg = tuple(it["bg"])
    if it.get("how") in PANELS:
        return panel_layer(it)
    if not it["ko"]:  # 윗줄 번역에 포함된 줄바꿈 꼬리: 원문만 가림
        rect = (x0 - 2, y0 - 2, x1 + 2, y1 + 2)
        img = Image.new("RGBA", (rect[2] - rect[0], rect[3] - rect[1]), bg + (255,))
        return img, rect[:2], rect
    size, lines = layout(it["ko"], it["box"], it["line_h"], *it["avail"], font, it.get("vertical", False))
    f = font(size)
    text_h = len(lines) * f.linespace
    ty = y0 + max(0, (y1 - y0 - text_h) // 2)
    text_w = max((f.measure(line) for line in lines), default=0)
    rect = (x0 - PAD, min(y0, ty) - PAD, max(x1, x0 + text_w) + PAD, max(y1, ty + text_h) + PAD)
    w, h = rect[2] - rect[0], rect[3] - rect[1]
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img, "RGBA")
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=min(RADIUS, h // 3), fill=bg + (255,))
    fg = text_color(bg, it.get("fg")) + (255,)
    for i, line in enumerate(lines):
        d.text((x0 - rect[0], ty - rect[1] + i * f.linespace), line, font=f.font, fill=fg)
    return img, rect[:2], rect


def natural_layers(it):
    """자연스럽게 그리기: [(원문을 지운 배경 조각, 자리, 0), (한국어 글자, 자리, 1)] — 상자 없이 원문 글자색으로.

    배경 조각(층 0)을 모든 항목에서 먼저 그리고 글자(층 1)를 그 위에 그린다: 조각 가장자리가 이웃 번역의 글자를
    가리지 않게 ('현재' 조각이 바로 아래 '임무 완료' 윗부분을 지웠음).
    짧은 라벨(버튼·탭)은 원문 자리 가운데에, 긴 글은 원문 왼쪽에 맞춘다. 배경과 글자색이 비슷하면 얇은 테두리.
    """
    x0, y0, x1, y1 = it["box"]
    patch, (px, py) = it["patch"]
    out = [(patch, (x0 + px, y0 + py), 0)]
    ko = it["ko"]
    if not ko:  # 줄바꿈 꼬리: 원문만 지움
        return out
    size, lines = layout(ko, it["box"], it["line_h"], *it["avail"], natural_font, it.get("vertical", False))
    f = natural_font(size)
    text_h = len(lines) * f.linespace
    text_w = max((f.measure(line) for line in lines), default=0)
    ty = y0 + (y1 - y0 - text_h) // 2
    short = len(lines) == 1 and len(it["zh"]) <= 8 and not it.get("vertical") and text_w <= (x1 - x0) * 1.4
    tx = x0 + ((x1 - x0) - text_w) // 2 if short else x0
    bg = tuple(it["bg"])
    fg = text_color(bg, it.get("fg"))
    o = 2 if abs(lum(fg) - lum(bg)) < 60 else 0  # 글자색이 배경과 비슷할 때만 테두리 (text_color가 대개 막아 줌)
    img = Image.new("RGBA", (text_w + o * 2 + 2, text_h + o * 2 + 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(img, "RGBA")
    edge = (0, 0, 0, 170) if lum(bg) >= 128 else (255, 255, 255, 170)
    for i, line in enumerate(lines):
        pos = (o + (((text_w - f.measure(line)) // 2) if short else 0), o + i * f.linespace)
        d.text(pos, line, font=f.font, fill=fg + (255,), **({"stroke_width": o, "stroke_fill": edge} if o else {}))
    out.append((img, (tx - o, ty - o), 1))
    return out


def item_layers(it):
    """번역 항목 하나를 그린 층들 [(그림, 왼쪽 위 좌표, 층)]. 층 0 = 원문 지우기, 1 = 번역 상자·글자, 2 = 정보 패널.

    화면 전체를 그릴 때 층 번호 순서로 쌓는다 (같은 층 안에서는 넘겨준 순서).
    """
    if STYLE == "natural" and it.get("patch") is not None and it.get("how") not in PANELS:
        return natural_layers(it)
    img, pos, _ = item_layer(it)
    return [(img, pos, 2 if it.get("how") in PANELS else 1)]


def panel_layer(it):
    """정보 패널: [(글, 색)] 줄. 색은 (r, g, b) 또는 등급 숫자(RARITY_COLOR).

    자리: 기준 상자(it['box']) 바로 아래, 자리가 없으면 위. place='left'면 기준 상자 왼쪽 (스테이지 상세 칸 옆).
    """
    rows = it["rows"]
    size = max(14, min(26, int(it["line_h"] * 0.7)))
    title, body = font(size, bold=True), font(size)
    width = max([title.measure(it["title"])] + [body.measure(t) for t, _ in rows]) + size * 2
    width = min(width, it["avail"][0])
    pad = size // 2
    h = title.linespace + len(rows) * body.linespace + pad * 3
    img = Image.new("RGBA", (int(width), int(h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img, "RGBA")
    d.rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=RADIUS * 2, fill=(18, 20, 26, 238),
                        outline=(255, 255, 255, 40))
    d.text((size, pad), it["title"], font=title.font, fill=(255, 255, 255, 255))
    y = pad * 2 + title.linespace
    for text, color in rows:
        while body.measure(text) > width - size * 2 and len(text) > 4:
            text = text[:-2] + "…"
        rgb = tuple(color) if isinstance(color, (tuple, list)) else RARITY_COLOR.get(color, (220, 220, 220))
        d.text((size, y), text, font=body.font, fill=rgb + (255,))
        y += body.linespace
    sw, sh = it["screen"]
    bx0, by0, bx1, by1 = it["box"]
    gap = size // 2
    if it.get("place") == "left":
        x = max(10, bx0 - gap * 2 - img.width)
        top = max(10, min(by0, sh - img.height - 10))
    else:
        x = max(10, min(bx0, sw - img.width - 10))
        top = by1 + gap if by1 + gap + img.height <= sh - 10 else max(10, by0 - gap - img.height)
    return img, (x, top), (x, top, x + img.width, top + img.height)


def compose(size, layers):
    """[(그림, 좌표, 투명도 0~1)] → 창 크기 RGBA 그림."""
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    for img, pos, alpha in layers:
        if alpha < 1:
            a = img.getchannel("A").point(lambda v: int(v * alpha))
            img = img.copy()
            img.putalpha(a)
        canvas.alpha_composite(img, dest=(int(pos[0]), int(pos[1])))
    return canvas


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p),
                                   wintypes.HANDLE, wintypes.DWORD]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.GetWindowLongW.restype = ctypes.c_long
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                                       ctypes.POINTER(wintypes.SIZE), wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                                       wintypes.COLORREF, ctypes.POINTER(_BLENDFUNCTION), wintypes.DWORD]


class LayeredWindow:
    """WS_EX_LAYERED 창에 RGBA 그림을 올린다 (창 크기가 같으면 DIB를 다시 쓰고, 바뀌면 새로 만든다)."""

    def __init__(self, hwnd):
        self.hwnd = hwnd
        self.size = None
        self.mem = self.bmp = self.old = None
        self.bits = ctypes.c_void_p()
        self.failures = 0

    def _alloc(self, w, h):
        self.free()
        screen = user32.GetDC(None)
        self.mem = gdi32.CreateCompatibleDC(screen)
        user32.ReleaseDC(None, screen)
        bmi = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        self.bmp = gdi32.CreateDIBSection(self.mem, ctypes.byref(bmi), 0, ctypes.byref(self.bits), None, 0)
        self.old = gdi32.SelectObject(self.mem, self.bmp)
        self.size = (w, h)

    def push(self, img, x, y):
        w, h = img.size
        if self.size != (w, h):
            self._alloc(w, h)
        a = np.asarray(img, dtype=np.uint16)
        alpha = a[..., 3:4]
        # UpdateLayeredWindow는 색에 투명도를 미리 곱한(premultiplied) BGRA를 받는다
        bgra = np.empty((h, w, 4), dtype=np.uint8)
        bgra[..., :3] = (a[..., 2::-1] * alpha // 255).astype(np.uint8)
        bgra[..., 3] = alpha[..., 0]
        ctypes.memmove(self.bits, bgra.ctypes.data, bgra.nbytes)
        ok = self._update(x, y, w, h)
        if not ok:
            # 실패하면 창이 그림 대신 Tk 기본 회색 바탕으로 보여 게임을 통째로 가린다 (관리자 권한 실행에서 겪음).
            # WS_EX_LAYERED를 껐다 켜서 레이어 상태를 처음으로 되돌린 뒤 한 번 더 해 본다
            err = ctypes.get_last_error()
            style = user32.GetWindowLongW(self.hwnd, -20)
            user32.SetWindowLongW(self.hwnd, -20, style & ~0x80000)
            user32.SetWindowLongW(self.hwnd, -20, style | 0x80000)
            ok = self._update(x, y, w, h)
            self.failures += 1
            if self.failures <= 3:
                print(f"UpdateLayeredWindow 실패 (오류 {err}) → 레이어 초기화 후 {'성공' if ok else '또 실패 ' + str(ctypes.get_last_error())}",
                      flush=True)
        return bool(ok)

    def _update(self, x, y, w, h):
        screen = user32.GetDC(None)
        blend = _BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_OVER, AC_SRC_ALPHA
        ok = user32.UpdateLayeredWindow(self.hwnd, screen, ctypes.byref(wintypes.POINT(x, y)),
                                        ctypes.byref(wintypes.SIZE(w, h)), self.mem,
                                        ctypes.byref(wintypes.POINT(0, 0)), 0, ctypes.byref(blend), 2)  # ULW_ALPHA
        user32.ReleaseDC(None, screen)
        return ok

    def free(self):
        if self.mem:
            gdi32.SelectObject(self.mem, self.old)
            gdi32.DeleteObject(self.bmp)
            gdi32.DeleteDC(self.mem)
            self.mem = self.bmp = self.old = None
            self.size = None


def attach_patch(it, arr):
    """자연스럽게 그리기면 항목에 원문을 지운 배경 조각을 붙인다 (그 화면 arr에서). 정보 패널은 해당 없음."""
    if STYLE == "natural" and it.get("how") not in PANELS and "patch" not in it:
        it["patch"] = make_patch(arr, it["box"], it["bg"], it["line_h"])
    return it
