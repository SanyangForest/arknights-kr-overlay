"""번역 상자를 픽셀 단위 투명도로 그린다 (레이어드 창 + UpdateLayeredWindow).

Tk의 투명색(-transparentcolor) 방식은 글자 가장자리를 투명한 바탕과 섞을 수 없어 계단처럼 깨지고 색 테두리가 남는다.
PIL로 창 전체 크기의 RGBA 그림을 그려 한 번에 올리면 부드러운 글자, 둥근 상자, 서서히 나타나기를 쓸 수 있다.
창 자체(클릭 통과, 캡처 제외)는 overlay.py가 만든 Tk 창을 그대로 쓴다.
"""

import ctypes
from ctypes import wintypes

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from fit import layout

FONT_PATH = "C:/Windows/Fonts/malgun.ttf"
BOLD_PATH = "C:/Windows/Fonts/malgunbd.ttf"
PAD = 3
RADIUS = 5
# 등급 색 (공개모집 패널)
RARITY_COLOR = {6: (255, 127, 39), 5: (255, 208, 64), 4: (205, 170, 255), 3: (170, 170, 170), 1: (190, 220, 255)}

# 함수 모양(argtypes)을 따로 정하므로 ctypes.windll 공용 객체가 아닌 별도 객체를 쓴다.
# 공용 객체를 바꾸면 overlay.grab_window처럼 기본 모양을 믿는 다른 코드가 깨진다 (GetDC가 64비트 핸들을 돌려줌)
user32 = ctypes.WinDLL("user32")
gdi32 = ctypes.WinDLL("gdi32")


class PilFont:
    """fit.layout이 쓰는 Tk 글꼴과 같은 모양(measure, metrics('linespace'))의 PIL 글꼴."""

    def __init__(self, size, bold=False):
        self.font = ImageFont.truetype(BOLD_PATH if bold else FONT_PATH, size)
        ascent, descent = self.font.getmetrics()
        self.linespace = ascent + descent

    def measure(self, text):
        return int(self.font.getlength(text))

    def metrics(self, what):
        return self.linespace


_fonts = {}


def font(size, bold=False):
    key = (size, bold)
    if key not in _fonts:
        _fonts[key] = PilFont(size, bold)
    return _fonts[key]


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
    if it.get("how") == "recruit":
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


def panel_layer(it):
    """공개모집 확정 조합 패널: [(글, 등급)] 줄을 등급색으로. 태그 무리(it['box']) 바로 아래, 자리가 없으면 위에 놓는다."""
    rows = it["rows"]
    size = max(14, min(26, int(it["line_h"] * 0.7)))
    title, body = font(size, bold=True), font(size)
    width = max(title.measure(it["title"]), *(body.measure(t) for t, _ in rows)) + size * 2
    width = min(width, it["avail"][0])
    pad = size // 2
    h = title.linespace + len(rows) * body.linespace + pad * 3
    img = Image.new("RGBA", (int(width), int(h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img, "RGBA")
    d.rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=RADIUS * 2, fill=(18, 20, 26, 238),
                        outline=(255, 255, 255, 40))
    d.text((size, pad), it["title"], font=title.font, fill=(255, 255, 255, 255))
    y = pad * 2 + title.linespace
    for text, rarity in rows:
        while body.measure(text) > width - size * 2 and len(text) > 4:
            text = text[:-2] + "…"
        d.text((size, y), text, font=body.font, fill=RARITY_COLOR.get(rarity, (220, 220, 220)) + (255,))
        y += body.linespace
    sw, sh = it["screen"]
    bx0, by0, bx1, by1 = it["box"]
    gap = size // 2
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
        screen = user32.GetDC(None)
        blend = _BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_OVER, AC_SRC_ALPHA
        ok = user32.UpdateLayeredWindow(self.hwnd, screen, ctypes.byref(wintypes.POINT(x, y)),
                                        ctypes.byref(wintypes.SIZE(w, h)), self.mem,
                                        ctypes.byref(wintypes.POINT(0, 0)), 0, ctypes.byref(blend), 2)  # ULW_ALPHA
        user32.ReleaseDC(None, screen)
        return bool(ok)

    def free(self):
        if self.mem:
            gdi32.SelectObject(self.mem, self.old)
            gdi32.DeleteObject(self.bmp)
            gdi32.DeleteDC(self.mem)
            self.mem = self.bmp = self.old = None
            self.size = None
