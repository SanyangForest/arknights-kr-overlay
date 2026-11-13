"""게임 창 캡처 시험: 화면 복사(BitBlt) 방식과 PrintWindow 방식을 비교한다.

게임 프로세스에는 접근하지 않고, 창 위치와 화면 픽셀만 읽는다.
"""

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

import numpy as np
from PIL import Image, ImageGrab

WINDOW_TITLE = "明日方舟"
OUT = Path(__file__).parent / "captures"

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
ctypes.windll.shcore.SetProcessDpiAwareness(2)  # 고해상도 배율에서도 실제 픽셀 좌표 사용


def find_window(title):
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        sys.exit(f"'{title}' 창을 찾지 못했습니다.")
    return hwnd


def client_rect_on_screen(hwnd):
    """창 테두리·제목줄을 뺀 게임 화면 영역의 화면 좌표."""
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    origin = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    return origin.x, origin.y, origin.x + rect.right, origin.y + rect.bottom


def grab_screen(bbox):
    return ImageGrab.grab(bbox=bbox, all_screens=True)


def grab_printwindow(hwnd, width, height):
    hdc_window = user32.GetDC(hwnd)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
    bitmap = gdi32.CreateCompatibleBitmap(hdc_window, width, height)
    gdi32.SelectObject(hdc_mem, bitmap)
    PW_CLIENTONLY, PW_RENDERFULLCONTENT = 0x1, 0x2
    ok = user32.PrintWindow(hwnd, hdc_mem, PW_CLIENTONLY | PW_RENDERFULLCONTENT)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(hdc_mem, bitmap, 0, height, buf, ctypes.byref(bmi), 0)
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(hwnd, hdc_window)
    img = Image.frombuffer("RGBA", (width, height), buf, "raw", "BGRA", 0, 1).convert("RGB")
    return ok, img


def describe(img):
    a = np.asarray(img).astype(np.int16)
    return f"{img.size[0]}x{img.size[1]}, 평균 밝기 {a.mean():.0f}, 표준편차 {a.std():.0f}"


def main():
    """게임 창이 맨 앞에 있을 때만 일정 간격으로 찍는다 (다른 창이 찍히지 않도록)."""
    import time

    interval, max_shots, timeout = 4.0, 8, 180
    OUT.mkdir(exist_ok=True)
    hwnd = find_window(WINDOW_TITLE)
    print(f"창 핸들 {hwnd}. 게임 창이 맨 앞에 있을 때만 {interval:.0f}초마다 최대 {max_shots}장 찍습니다.", flush=True)

    shots, deadline, last = 0, time.time() + timeout, 0.0
    while shots < max_shots and time.time() < deadline:
        time.sleep(0.5)
        if user32.GetForegroundWindow() != hwnd or time.time() - last < interval:
            continue
        time.sleep(0.5)  # 창 전환 직후 화면이 다 그려지도록
        if user32.GetForegroundWindow() != hwnd:
            continue
        bbox = client_rect_on_screen(hwnd)
        img = grab_screen(bbox)
        shots += 1
        last = time.time()
        path = OUT / f"cn_{shots:02d}.png"
        img.save(path)
        print(f"[{shots}] {path.name}: {describe(img)}", flush=True)
    print("끝" if shots else "게임 창이 맨 앞에 오지 않아 찍지 못했습니다.")


if __name__ == "__main__":
    main()
