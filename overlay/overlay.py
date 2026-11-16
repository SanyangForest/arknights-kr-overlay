"""명일방주 중국 서버 화면 위에 한국어를 덮어씌우는 오버레이 (B 방식 시제품).

- 게임 창 화면을 캡처해서 OCR → zh→ko 사전 매칭 → 원문 자리에 한국어 상자를 그린다.
- 오버레이 창은 클릭을 통과시키므로, 한국어 상자를 눌러도 아래 게임이 눌린다.
- 오버레이 자신은 화면 캡처에서 제외되므로 다시 인식되지 않는다.
  방송·화면 공유에 보이기(show_in_capture)를 켜면 게임 창만 따로 찍고(PrintWindow) 오버레이를 캡처에 보이게 한다.
- 게임 파일·프로세스에는 접근하지 않고, 게임에 입력을 보내지도 않는다.

단축키: F9 표시 켜기/끄기, F10 종료

사용법: python overlay.py <사전 json>
"""

import ctypes
import json
import queue
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path

ctypes.windll.shcore.SetProcessDpiAwareness(2)  # tkinter보다 먼저: 실제 픽셀 좌표 사용

import cv2
import numpy as np
import win32con
import win32gui
from PIL import Image, ImageGrab

import datastore
import depot
import drops
import owned
import paths
import render
from fit import free_space
from layout import merge_lines
from matcher import HAN, Matcher, display, edge_of, normalize
from ocr_engine import CachedOCR, make_ocr, providers
from recruit import Recruit
from screens import classify, labels_for, load_screens
from story import Story
from vocab import LEARN_KINDS, ScreenVocab

WINDOW_TITLE = "明日方舟"
FADE_IN = 0.12            # 새 번역 상자가 서서히 나타나는 시간 (초)
INTERVAL = 0.2            # 화면 확인 간격 (초). 너무 짧으면 게임과 GPU를 다툼
CHANGE_THRESHOLD = 1.5    # 축소 화면 평균 밝기 차이가 이보다 크면 다시 인식
PIXEL_CHANGE = 25         # 축소 화면에서 이만큼 밝기가 바뀐 픽셀을 '바뀐 픽셀'로 셈
CHANGED_RATIO = 0.002     # 바뀐 픽셀이 이 비율을 넘으면 다시 인식 (대사 한 줄 등 작은 변화)
FORCE_REFRESH = 2.0       # 바뀐 게 없어 보여도 이 시간(초)마다 다시 인식
# 이보다 크면 화면 전환으로 보고 상자를 즉시 지움. 움직이는 부분(MOTION)은 빼고 잰다
SCENE_CHANGE = 25.0
SCENE_FRACTION = 0.6      # 움직이는 부분까지 포함해 화면의 이 비율 넘게 크게 바뀌어도 화면 전환 (가만히 있는 화면은 5% 안팎)
# Live2D 같은 움직이는 배경과 실제 화면 변화를 구별: 최근 몇 초 동안 자주 바뀐 픽셀은 '움직이는 부분'으로 보고
# 다시 인식할지·화면이 넘어갔는지 판단에서 뺀다. 화면이 실제로 넘어가면 다시 배운다.
# 0.2초마다 네 번 넘게 잇달아 바뀌어야 움직이는 부분이 된다: 0.5초 안팎의 화면 전환 효과까지 배워 버리면
# 그 자리의 옛 번역을 '움직이는 자리라 확인 못 함'으로 보고 몇 초씩 남겨 새 화면 글자와 겹침
MOTION_ALPHA = 0.04       # 움직임 기억의 갱신 비율 (대략 5초 동안의 움직임을 기억)
MOTION_MAX = 0.12         # 최근 이 비율 넘게 바뀐 픽셀은 움직이는 부분
MOTION_ITEM = 0.3         # 상자 자리의 이 비율 넘게 움직이는 부분이면 화면 비교로 지우지 않고 OCR만 믿음
MISSES_TO_REMOVE = 2      # 연속으로 이만큼 안 보이면 상자를 지움 (깜빡임 방지)
# OCR이 못 읽은 상자는, 처음 그릴 때 저장한 원문 글자 그림을 그 주변(±SHIFT_SEARCH 픽셀)에서 찾아 본다.
# 홈 메뉴판처럼 마우스를 따라 몇 픽셀 움직여도 같은 글자로 알아보고 번역 상자도 따라 옮긴다.
SHIFT_SEARCH = 14
MATCH_SAME = 0.75         # 이만큼 닮았으면 글자가 그대로 있는 것: 지우지 않음
MATCH_GONE = 0.4          # 이보다 덜 닮았으면 다른 화면으로 넘어간 것: 바로 지움
KEEP_UNSEEN = 2.0         # 그림으로 확인할 수 없는 상자는 OCR이 이 시간(초) 넘게 못 읽으면 지움
# 새로 읽은 글자 상자가 옛 상자와 (작은 쪽 넓이의) 이 비율 넘게 겹치면 옛 상자는 이미 사라진 글자: 바로 지움.
# 게임은 글자 위에 다른 글자를 겹쳐 쓰지 않으므로. 위아래로 살짝 닿은 이웃('5天' / '仓库')은 15% 안팎
OVERLAP_REPLACE = 0.25
MOVE_TOLERANCE = 6        # 이 픽셀 이하로 움직이면 상자 위치를 그대로 둠
SAME_PLACE_IOU = 0.4      # 번역이 같고 상자가 이만큼 겹치면 같은 자리로 보고 처음 상자를 유지
SHORT_TEXT = 8            # 이 글자 수 이하는 같은 자리에서 다른 번역이 두 번 연속 나와야 바꿈 (대사 같은 긴 글은 바로 바꿈)
WDA_NONE, WDA_EXCLUDEFROMCAPTURE = 0x0, 0x11
VK_F9, VK_F10 = 0x78, 0x79
LOG = paths.USER / "overlay_log.txt"
MISSING = paths.USER / "overlay_missing.tsv"  # 번역하지 못한 글 (시각, 화면, 원문)
SNAPSHOT_FONT = "C:/Windows/Fonts/malgun.ttf"
SETTINGS = paths.SETTINGS


def load_settings():
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8-sig"))  # 메모장으로 고치면 BOM이 붙음
    except (OSError, ValueError):
        return {}

user32 = ctypes.windll.user32


def client_rect(hwnd):
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    origin = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    return origin.x, origin.y, rect.right, rect.bottom


def find_game_window():
    """보이는 명일방주 게임 창 (없으면 0).

    런처(Games.exe, Qt 창)도 같은 제목의 창을 숨겨 두므로 FindWindow로 찾으면 런처에 붙어 버린다.
    """
    found = []

    def check(h, _):
        if win32gui.GetWindowText(h) == WINDOW_TITLE and win32gui.IsWindowVisible(h)                 and not win32gui.GetClassName(h).startswith("Qt"):
            l, t, r, b = win32gui.GetWindowRect(h)
            if r - l >= 200 and b - t >= 200:
                found.append(h)

    win32gui.EnumWindows(check, None)
    return found[0] if found else 0


class _BMI(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


def grab_window(hwnd, w, h):
    """게임 창 내용만 찍는다 (위에 덮인 오버레이·다른 창은 안 찍힘). 실패하면 None.

    게임이 관리자 권한으로 돌면 같은 권한으로 실행해야 한다 (아니면 접근 거부로 실패).
    """
    gdi32 = ctypes.windll.gdi32
    hdc = user32.GetDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mem, bmp)
    try:
        if not user32.PrintWindow(hwnd, mem, 0x1 | 0x2):  # PW_CLIENTONLY | PW_RENDERFULLCONTENT
            return None
        buf = ctypes.create_string_buffer(w * h * 4)
        bmi = _BMI(ctypes.sizeof(_BMI), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        if not gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bmi), 0):
            return None
        return Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(hwnd, hdc)


def ocr_engine_providers(worker):
    """일꾼의 OCR이 쓰는 장치 목록 (DmlExecutionProvider = 그래픽카드)."""
    try:
        return ", ".join(providers(worker.ocr.ocr))
    except Exception as e:  # noqa: BLE001
        return f"알 수 없음 ({e})"


def boxes_overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow",
                 "TopLevelWindowForOverflowXamlIsland", "#32768"}  # 바탕화면·작업 표시줄·트레이·메뉴


COVER_HIDE_ALL = 0.6  # 맨 앞의 다른 창이 게임 화면을 이 비율 넘게 덮으면 오버레이를 통째로 숨김


def foreground_cover(hwnd):
    """맨 앞의 다른 프로그램 창이 게임 화면 위에 겹친 부분: ((x0, y0, x1, y1) 게임 화면 좌표, 덮은 비율). 없으면 None.

    다른 모니터에 있거나 바탕화면·작업 표시줄이면 None.
    """
    fg = user32.GetForegroundWindow()
    if not fg or fg == hwnd:
        return None
    try:
        if win32gui.GetClassName(fg) in SHELL_CLASSES:
            return None
        fx0, fy0, fx1, fy1 = win32gui.GetWindowRect(fg)
        gx0, gy0, gx1, gy1 = win32gui.GetWindowRect(hwnd)
    except win32gui.error:
        return None
    # 게임 창 안에 딱 들어가는 창은 게임이 띄운 것 (공지·이벤트 공지는 다른 프로세스 PlatformProcess가 게임 위에 띄움)
    if fx0 >= gx0 - 4 and fy0 >= gy0 - 4 and fx1 <= gx1 + 4 and fy1 <= gy1 + 4:
        return None
    x, y, w, h = client_rect(hwnd)
    ox0, oy0 = max(x, fx0) - x, max(y, fy0) - y
    ox1, oy1 = min(x + w, fx1) - x, min(y + h, fy1) - y
    if ox1 <= ox0 or oy1 <= oy0 or w <= 0 or h <= 0:
        return None
    return (ox0, oy0, ox1, oy1), (ox1 - ox0) * (oy1 - oy0) / (w * h)


POPUP_TITLE = "UIPlatform"  # 게임이 따로 띄우는 창 (PlatformProcess, Qt): 공지, 위수 협의 전적 등


def inside_game(h, hwnd):
    """창 h가 게임 창 안에 딱 들어가는가 (게임이 띄운 팝업)."""
    try:
        x0, y0, x1, y1 = win32gui.GetWindowRect(h)
        gx0, gy0, gx1, gy1 = win32gui.GetWindowRect(hwnd)
    except win32gui.error:
        return False
    return x0 >= gx0 - 4 and y0 >= gy0 - 4 and x1 <= gx1 + 4 and y1 <= gy1 + 4


def game_popups(hwnd):
    """게임 창 위에 떠 있는 게임 쪽 팝업 창들. 게임 창만 찍으면(PrintWindow) 안 보이므로 따로 찍어 합친다."""
    found = []

    def check(h, _):
        if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) == POPUP_TITLE and inside_game(h, hwnd):
            found.append(h)

    win32gui.EnumWindows(check, None)
    return found


def paste_popups(img, hwnd, x, y):
    """게임 창만 찍은 그림(img, 화면 위 자리 x, y)에 게임 쪽 팝업 창을 찍어 같은 자리에 붙인다."""
    for p in game_popups(hwnd):
        px, py, pw, ph = client_rect(p)
        shot = grab_window(p, pw, ph) if pw > 0 and ph > 0 else None
        if shot is not None and np.asarray(shot.convert("L").resize((64, 36))).std() >= 4:
            img.paste(shot, (px - x, py - y))


def is_game_foreground(hwnd):
    """게임 창, 또는 게임이 띄운 팝업(UIPlatform)이 맨 앞인가."""
    fg = user32.GetForegroundWindow()
    if fg == hwnd:
        return True
    try:
        return bool(fg) and win32gui.GetWindowText(fg) == POPUP_TITLE and inside_game(fg, hwnd)
    except win32gui.error:
        return False


def game_active(hwnd, always=False):
    """게임 창을 인식·표시해도 되는가. 기본은 게임이 맨 앞일 때만.

    always(게임을 다른 모니터에 띄워 두고 쓰는 경우)면 최소화되지 않았고 다른 프로그램 창이 게임 화면 대부분을 덮지 않을 때.
    일부만 덮으면 그 창과 겹치는 번역 상자만 숨긴다 (Overlay.cover). 안 그러면 상자가 그 창(디스코드 등) 위에 떠 화면 공유에도 찍힌다.
    """
    if always:
        if user32.IsIconic(hwnd):
            return False
        cover = foreground_cover(hwnd)
        return not (cover and cover[1] > COVER_HIDE_ALL)
    return is_game_foreground(hwnd)


def iou(a, b):
    """두 상자가 겹치는 정도 (겹친 넓이 / 합친 넓이)."""
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def covered_ratio(a, b):
    """상자 a 중 b와 겹치는 비율."""
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    area = (a[2] - a[0]) * (a[3] - a[1])
    return ix * iy / area if area > 0 else 0.0


def overlap_small(a, b):
    """두 상자가 겹친 넓이 / 작은 상자의 넓이."""
    return max(covered_ratio(a, b), covered_ratio(b, a))


def crop_of(gray, box):
    """상자 자리의 원문 글자 그림. 너무 작거나 밋밋하면(글자 모양이 없으면) None."""
    x0, y0, x1, y1 = (int(v) for v in box)
    crop = gray[max(0, y0):y1, max(0, x0):x1]
    if crop.shape[0] < 6 or crop.shape[1] < 6 or crop.std() < 12:
        return None
    return crop.copy()


def find_crop(gray, box, crop):
    """저장한 글자 그림을 box 주변에서 찾아 (닮은 정도, dx, dy)."""
    x0, y0 = int(box[0]), int(box[1])
    h, w = crop.shape
    rx0, ry0 = max(0, x0 - SHIFT_SEARCH), max(0, y0 - SHIFT_SEARCH)
    region = gray[ry0:y0 + h + SHIFT_SEARCH, rx0:x0 + w + SHIFT_SEARCH]
    if region.shape[0] < h or region.shape[1] < w:
        return 0.0, 0, 0
    res = cv2.matchTemplate(region, crop, cv2.TM_CCOEFF_NORMED)
    _, score, _, (mx, my) = cv2.minMaxLoc(res)
    return float(score), rx0 + mx - x0, ry0 + my - y0


def blurred(gray, box, crop, dx=0, dy=0, ratio=0.35):
    """처음 그릴 때의 글자 그림(crop)보다 지금 그 자리가 훨씬 흐린가 (선명도 = 라플라시안 분산)."""
    x0, y0 = int(box[0]) + dx, int(box[1]) + dy
    h, w = crop.shape
    cur = gray[max(0, y0):y0 + h, max(0, x0):x0 + w]
    if cur.shape != crop.shape:
        return False
    before = cv2.Laplacian(crop, cv2.CV_32F).var()
    return before > 50 and cv2.Laplacian(cur, cv2.CV_32F).var() < before * ratio


def key_pressed(vk):
    return user32.GetAsyncKeyState(vk) & 1


def sample_bg(arr, box, pad=1):
    """원문 상자 테두리 바로 바깥의 중앙값 색 = 덮을 배경색.

    너무 멀리서 가져오면 글자가 놓인 태그·버튼 밖의 색을 집으므로 1px 바깥만 본다.
    """
    x0, y0, x1, y1 = box
    h, w = arr.shape[:2]
    x0, y0, x1, y1 = max(0, x0 - pad), max(0, y0 - pad), min(w - 1, x1 + pad), min(h - 1, y1 + pad)
    edge = np.concatenate([arr[y0, x0:x1], arr[y1, x0:x1], arr[y0:y1, x0], arr[y0:y1, x1]])
    return tuple(int(v) for v in np.median(edge, axis=0))


def sample_fg(arr, box, bg):
    """원문 글자색: 상자 안에서 배경색과 가장 다른 픽셀들의 중앙값. 글자 모양이 안 보이면 None."""
    x0, y0, x1, y1 = (int(v) for v in box)
    region = arr[max(0, y0):y1, max(0, x0):x1].reshape(-1, 3).astype(np.int32)
    if len(region) < 20:
        return None
    dist = np.abs(region - np.array(bg)).sum(axis=1)
    far = region[dist > 120]
    if len(far) < len(region) * 0.03:
        return None
    top = far[np.argsort(np.abs(far - np.array(bg)).sum(axis=1))[-max(1, len(far) // 4):]]
    return tuple(int(v) for v in np.median(top, axis=0))


GAP_SPLIT = 0.8  # OCR 한 줄 안의 빈 간격이 글자 높이의 이 배 넘으면 따로 떨어진 글로 보고 나눈다


def _char_width(c):
    """글자 폭 어림 (한자·전각 1, 영문·숫자 0.55, 공백 0.3)."""
    if c.isspace():
        return 0.3
    return 0.55 if ord(c) < 128 else 1.0


def split_gaps(result, arr, keep=None):
    """OCR이 떨어져 있는 두 글을 한 줄로 읽은 것('自动 OFF' + '跳过' 버튼)을 빈 간격에서 나눈다.

    RapidOCR은 글자별 위치를 주지 않으므로 상자 안 열마다 글자 픽셀이 있는지 보고, 넓은 빈 열이 있으면
    그 위치까지의 글자 폭 어림으로 원문을 자른다. 양쪽 모두 글자가 남을 때만 나눈다.
    keep(text)가 참인 줄(사전에 통째로 있는 문장)은 나누지 않는다: 전각 쉼표 뒤도 넓게 비어 문장이 잘렸음.
    """
    out = []
    gray = None
    for box, text, score in result:
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        x0, y0, x1, y1 = int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))
        h = y1 - y0
        if len(text) < 3 or h < 8 or x1 - x0 < h * 3 or not HAN.search(text) or (keep and keep(text)):
            out.append((box, text, score))
            continue
        if gray is None:
            gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY).astype(np.int16)
        crop = gray[max(0, y0):y1, max(0, x0):x1]
        if crop.size == 0:
            out.append((box, text, score))
            continue
        border = np.concatenate([crop[0], crop[-1], crop[:, 0], crop[:, -1]])
        ink = (np.abs(crop - np.median(border)) > 40).sum(axis=0) > 0
        cols = np.flatnonzero(ink)
        if len(cols) < 2:
            out.append((box, text, score))
            continue
        first, last = cols[0], cols[-1]
        gaps = np.diff(cols)
        i = int(np.argmax(gaps))
        if gaps[i] < h * GAP_SPLIT:
            out.append((box, text, score))
            continue
        gap_l, gap_r = cols[i] + 1, cols[i + 1]
        frac = ((gap_l + gap_r) / 2 - first) / max(1, last - first)
        widths = np.cumsum([_char_width(c) for c in text])
        cut = int(np.argmin(np.abs(widths / widths[-1] - frac))) + 1
        left, right = text[:cut].strip(), text[cut:].strip()
        if not left or not right:
            out.append((box, text, score))
            continue
        lb = [[x0, y0], [x0 + gap_l, y0], [x0 + gap_l, y1], [x0, y1]]
        rb = [[x0 + gap_r, y0], [x1, y0], [x1, y1], [x0 + gap_r, y1]]
        # 나눈 쪽에도 간격이 더 있을 수 있으므로 다시 본다
        out += split_gaps([(lb, left, score), (rb, right, score)], arr, keep)
    return out


def item_from(m, box, how, zh, ko, arr, **extra):
    """번역 상자 항목. 배경색과 원문 글자색을 함께 잰다."""
    bg = sample_bg(arr, box)
    return {"key": normalize(zh) if how != "label" else "label:" + zh, "box": box, "line_h": int(m["line_h"]),
            "vertical": m.get("vertical", False), "zh": m.get("text", zh), "how": how, "ko": ko,
            "bg": bg, "fg": sample_fg(arr, box, bg) if ko else None, **extra}


class Worker(threading.Thread):
    """화면이 바뀌면 OCR → 사전 매칭을 하고 결과를 큐에 넣는다."""

    def __init__(self, game, matcher, out, offline=False):
        super().__init__(daemon=True)
        self.game, self.matcher, self.out, self.offline = game, matcher, out, offline
        self.ocr = CachedOCR(make_ocr(gpu=True))
        self.screens = load_screens()
        recruit = datastore.json_file("recruit.json")
        self.recruit = Recruit(recruit) if recruit else None
        if not offline:  # 녹화 화면 재생(replay.py)에서는 받지 않음
            drops.start_refresh_thread()  # 펭귄 드롭 통계가 하루 넘었으면 새로 받음 (다음 실행부터 반영)
        self.drops = drops.Drops()
        self.story = Story(datastore.story_db(), nickname=matcher.nickname)  # 스토리 따라가기 (out/story.db가 없으면 꺼짐)
        self.vocab = ScreenVocab(matcher.nickname)      # 화면별 낱말장 (일부만 읽힌 글 알아보기)
        self.owned = owned.Owned()  # 보유 오퍼레이터 (오퍼레이터 목록 화면을 넘기면 기록됨, record)
        self.char_names = None      # 중국어 이름 → char_id (처음 오퍼레이터 목록을 볼 때 읽음)
        self.depot = None           # 창고 인식 (처음 창고를 볼 때 템플릿을 읽음)
        self.enabled = True
        self.always = False
        self.last_thumb = None
        self.prev_thumb = None
        self.motion = np.zeros((108, 192), dtype=np.float32)  # 픽셀별 최근 변화 빈도
        self.last_ocr = 0.0
        self.recorder = None
        self.missing = set()  # 이번 실행에서 번역하지 못한 글 (overlay_missing.tsv에 한 번씩 적음)
        self.running = True
        # 방송·화면 공유에 보이기: want=켜 달라는 요청, window_capture=게임 창만 찍는 중.
        # 오버레이가 캡처 제외를 다시 걸 때까지는 화면을 찍지 않는다 (번역 상자가 찍혀 원문을 가리지 않게)
        self.want_window_capture = False
        self.window_capture = False
        self.capture_retry_at = 0.0  # 실패하면 잠시 뒤 다시 (게임 최소화·화면 전환 중이면 검은 그림이 나옴)
        self.capture_error = None
        self.excluded = threading.Event()
        self.excluded.set()

    def _check_window_capture(self, x, y, w, h):
        """게임 창만 찍기가 되는지 (권한이 없으면 실패, 그려지지 않는 창이면 검은 화면).

        화면 복사와 비교하지 않는다: 켤 때 다른 창(Claude 등)이 게임 위에 있으면 화면 쪽만 다른 그림이라 늘 실패했음.
        """
        img = grab_window(self.game, w, h)
        if img is None:
            return "게임 창을 따로 찍지 못함 (게임이 관리자 권한이면 오버레이도 관리자 권한으로 실행)"
        if np.asarray(img.convert("L").resize((192, 108)), dtype=np.float32).std() < 4:
            return "게임 창을 따로 찍은 그림이 비어 있음 (검은 화면)"
        return None

    def run(self):
        while self.running:
            time.sleep(INTERVAL)
            if not self.enabled or not game_active(self.game, self.always):
                continue
            x, y, w, h = client_rect(self.game)
            if w < 100 or h < 100:
                continue
            if self.want_window_capture != self.window_capture:
                if self.want_window_capture and time.time() >= self.capture_retry_at:
                    err = self._check_window_capture(x, y, w, h)
                    self.window_capture = err is None
                    if err:
                        self.capture_retry_at = time.time() + 10  # 켜 달라는 요청은 그대로 두고 10초 뒤 다시
                    if err != self.capture_error or not err:
                        self.out.put(("capture_mode", (self.window_capture, err)))
                    self.capture_error = err
                else:
                    self.window_capture = False
            if not self.window_capture and not self.excluded.wait(1):
                continue
            try:
                img = grab_window(self.game, w, h) if self.window_capture else None
                if img is not None:
                    paste_popups(img, self.game, x, y)
                if self.window_capture and img is None:
                    # 도중에 실패하면(권한·창 상태) 오버레이가 다시 캡처에서 빠진 뒤에 화면 복사로 돌아가고, 10초 뒤 다시 시도
                    self.window_capture = False
                    self.capture_retry_at = time.time() + 10
                    self.capture_error = "게임 창을 따로 찍다가 실패해 원래 방식으로 돌아감"
                    self.excluded.clear()
                    self.out.put(("capture_mode", (False, self.capture_error)))
                    continue
                if img is None:
                    img = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
            except OSError:
                # 잠금 화면·UAC 확인 창(보안 데스크톱)이 떠 있는 동안은 캡처가 실패한다. 잠시 뒤 다시
                time.sleep(1)
                continue
            thumb = np.asarray(img.convert("L").resize((192, 108)), dtype=np.float32)
            # 0.2초마다 바뀐 픽셀을 누적해 '늘 움직이는 부분'(Live2D, 배경 효과)을 배운다
            if self.prev_thumb is not None:
                moving = (np.abs(thumb - self.prev_thumb) > PIXEL_CHANGE).astype(np.float32)
                self.motion += MOTION_ALPHA * (moving - self.motion)
            self.prev_thumb = thumb
            static = self.motion < MOTION_MAX
            if time.time() - getattr(self, "last_motion_log", 0) > 10:
                self.last_motion_log = time.time()
                print(f"움직이는 부분 {1 - static.mean():.0%}", flush=True)
            if self.last_thumb is not None:
                # 움직이는 부분은 빼고, 평소 가만히 있는 부분(UI·글자)이 바뀌었는지만 본다
                delta = np.abs(thumb - self.last_thumb) * static
                diff = delta.sum() / max(1, static.sum())
                # 대사 한 줄처럼 작은 변화도 잡도록 '크게 바뀐 픽셀 비율'도 본다
                changed_ratio = (delta > PIXEL_CHANGE).mean()
                whole_ratio = (np.abs(thumb - self.last_thumb) > PIXEL_CHANGE).mean()
            else:
                diff, changed_ratio, whole_ratio = 99.0, 1.0, 1.0
            stale = time.time() - self.last_ocr > FORCE_REFRESH
            if diff < CHANGE_THRESHOLD and changed_ratio < CHANGED_RATIO and not stale:
                continue
            self.last_thumb = thumb
            self.last_ocr = time.time()
            if diff >= SCENE_CHANGE or whole_ratio >= SCENE_FRACTION:
                self.out.put(("clear", None))
                self.motion[:] = 0  # 새 화면의 움직이는 부분은 다시 배운다
                print(f"화면 전환 (변화 {diff:.1f}, 바뀐 비율 {whole_ratio:.0%})", flush=True)

            t = time.perf_counter()
            items, lines = self.analyze(np.asarray(img), w, h)
            ms = (time.perf_counter() - t) * 1000
            self.out.put(("result", ((x, y, w, h), items, ms, img, ~static)))
            if self.recorder:
                self.recorder.save(img, lines)

    def analyze(self, arr, w, h):
        """게임 화면 한 장 → (번역 상자 항목들, 합친 OCR 줄들). 녹화 화면 재생(replay.py)도 이것을 쓴다."""
        result, _ = self.ocr(arr)
        # 사전에서 통째로 찾은 줄은 그대로 (나눠 찾기·조각 번역으로 겨우 맞춘 줄만 나눠 본다)
        result = split_gaps(result or [], arr, lambda t: (self.matcher.lookup(t) or ("split",))[0] not in ("split", "segment"))
        items = []
        # 지금 어느 화면인지 알면 그 화면에서 자주 틀리는 OCR 문구를 바로잡을 수 있다 (ocr_aliases.tsv)
        screen = classify([text for _, text, _ in result or []], self.screens)
        if not self.offline:  # 녹화 재생으로 보유 오퍼레이터·창고 기록을 바꾸지 않게
            self.record(result or [], arr)
        lines = merge_lines(result or [], w, self.matcher.can_continue, self.matcher.is_entry,
                            lambda t: self.matcher.lookup(t, screen) is not None)
        all_boxes = [tuple(int(v) for v in (m["x0"], m["y0"], m["x1"], m["y1"])) for m in lines]
        # 스토리 대사는 대본 순서로 따라가며 번역 (글자가 다 나오기 전에도 대사 전체 번역)
        done = self.story_items(lines, all_boxes, w, h, arr, items) if self.story else set()
        for i, (m, box) in enumerate(zip(lines, all_boxes)):
            if i in done:
                continue
            hit = self.matcher.lookup(m["text"], screen, edge_of(box, w))
            if not hit:
                # 윗줄에서 문장 전체를 이미 번역했으면 줄바꿈된 꼬리는 원문만 가린다
                # (꼬리 줄도 원래 문장을 이어받아, 그 아래 세 번째 줄도 꼬리로 알아본다)
                above = next((it for it in items if -m["line_h"] * 0.5 <= box[1] - it["box"][3] < m["line_h"] * 1.5
                              and it["box"][0] <= box[2] and box[0] <= it["box"][2]
                              and self.matcher.is_tail_of(m["text"], it["full_zh"])), None)
                key = None if above else self.vocab.guess(screen, m["text"])
                if above:
                    items.append(item_from({**m, "vertical": False}, box, "tail", m["text"], "", arr,
                                           avail=(box[2] - box[0], box[3] - box[1]), full_zh=above["full_zh"]))
                elif key and key in self.matcher.table:
                    # 잘리거나 가려져 일부만 읽힌 글: 이 화면에 나오는 문구 중 하나로 정해지면 그 번역 ('造站' → 制造站)
                    zh, ko = self.matcher.table[key]
                    items.append(item_from(m, box, "screen", m["text"], display(ko), arr,
                                           avail=free_space(box, all_boxes, w, h), full_zh=zh))
                else:
                    self.note_missing(m["text"], screen)
                continue
            if hit[0] in LEARN_KINDS and not self.offline:
                self.vocab.learn(screen, hit[1])
            items.append(item_from(m, box, hit[0], m["text"], hit[2], arr,
                                   avail=free_space(box, all_boxes, w, h), full_zh=hit[1]))
        # OCR이 못 읽는 그림 글자(홈의 '终端' 로고 등)는 화면 지도에 적힌 자리에 그린다.
        # OCR이 같은 글자를 읽었어도 항상 고정 글자 쪽을 쓴다 (둘이 번갈아 그려지면 위치가 흔들림)
        for box, zh, ko, vertical in labels_for(screen, self.screens, w, h, result or [], arr):
            # 같은 글자를 OCR이 읽은 것 (조각으로 읽은 것 포함: 采购中心 → '中采', '心购')
            same = [it for it in items if boxes_overlap(box, it["box"]) and
                    (it["ko"] == ko or it["full_zh"] == zh or set(normalize(it["zh"])) <= set(zh))]
            # 다른 번역이 그 자리를 대부분 덮고 있을 때만 건너뜀 (말풍선이 살짝 닿은 정도는 그림)
            if any(covered_ratio(box, it["box"]) >= 0.5 for it in items if it not in same):
                continue
            for it in same:
                items.remove(it)
            bh = box[3] - box[1]
            line_h = int(bh / len(zh)) if vertical else int(bh / max(1, round(len(zh) / 2)))
            items.append(item_from({"line_h": line_h, "vertical": vertical, "text": zh}, box, "label", zh, ko, arr,
                                   avail=free_space(box, all_boxes + [box], w, h), full_zh=zh))
        for panel in (self.recruit_panel(result or [], w, h),
                      self.drops.panel(result or [], w, h) if self.drops else None):
            if panel:
                items.append(panel)
        title = self.story.title_panel() if self.story else None
        if title:
            items.append({"key": "story_title", "how": "panel", "box": (int(w * 0.06), int(h * 0.11), int(w * 0.5), int(h * 0.11)),
                          "line_h": max(20, int(h * 0.03)), "vertical": False, "zh": "", "full_zh": "", "ko": title,
                          "rows": [], "title": title, "screen": (w, h), "avail": (w - 20, h), "bg": (18, 20, 26), "fg": None})
        return items, lines

    def story_items(self, lines, boxes, w, h, arr, items):
        """스토리 대사·화자 이름을 대본에서 찾아 번역 상자로 넣는다. 처리한 줄 번호들을 돌려준다.

        한 대사가 여러 줄로 읽히면(줄바꿈) 한 상자로 합치고, 아직 나오는 중인 대사는 다 나왔을 때의 자리만큼 상자를 잡아
        처음부터 대사 전체의 번역을 보여 준다.
        """
        ui = any(("跳过" in m["text"] or "自动" in m["text"]) and m["y1"] < h * 0.15 for m in lines)
        got = self.story.observe({i: m["text"] for i, m in enumerate(lines)}, ui)
        groups = {}
        for i, g in got.items():
            if g["kind"] == "name":
                items.append(item_from(lines[i], boxes[i], "story", lines[i]["text"], g["ko"], arr,
                                       avail=free_space(boxes[i], boxes, w, h), full_zh=g["zh"]))
            else:
                groups.setdefault(g["unit"], []).append(i)
        done = {i for i, g in got.items() if g["kind"] == "name"}
        content_w = min(w, h * 16 / 9)  # 게임 창이 16:9보다 넓으면 양옆은 검은 띠
        for unit, idxs in groups.items():
            main = [i for i in idxs if got[i]["kind"] != "part"]
            if not main:
                continue  # 다른 대사 안에 우연히 든 글('好的')만 맞은 것: 사전 쪽에 맡김
            idxs.sort(key=lambda i: (boxes[i][1], boxes[i][0]))
            g = got[main[0]]
            box = tuple(f(boxes[i][k] for i in idxs) for k, f in enumerate((min, min, max, max)))
            line_h = max(lines[i]["line_h"] for i in idxs)
            first = lines[idxs[0]]["text"]
            if g["ukind"] in ("dialog", "multiline", "narration"):
                box = self.story.full_box(unit, box, line_h, first, g["zh"], content_w * 0.57, (w, h))
            others = [b for j, b in enumerate(boxes) if j not in idxs]
            text = "".join(lines[i]["text"] for i in idxs)
            items.append(item_from({"line_h": line_h, "vertical": False, "text": text}, box, "story", text, g["ko"], arr,
                                   avail=free_space(box, others, w, h), full_zh=g["zh"]))
            done |= set(idxs)
        return done

    def record(self, result, arr):
        """오퍼레이터 목록·창고 화면이면 보이는 오퍼레이터·아이템을 기록한다 (사용자가 직접 넘기는 동안 저절로).

        게임에 입력은 보내지 않는다. 오퍼레이터 목록은 정렬 버튼('稀有度' + '等级'/'自定义排序'),
        창고는 탭('养成材料' + '全部'/'消耗物品'/'基础物品')으로 알아본다.
        """
        texts = {normalize(t) for _, t, _ in result}
        if "稀有度" in texts and texts & {"等级", "自定义排序"}:
            if self.char_names is None:
                self.char_names = owned.char_names()
            seen = {}
            for _box, text, _s in result:
                key = normalize(text)
                if key not in self.char_names:
                    hit = self.matcher.lookup(text)
                    key = hit[1] if hit and hit[0] == "alias" else key
                if key in self.char_names:
                    seen[self.char_names[key]] = key
            have = owned.load()
            new = {k: v for k, v in seen.items() if k not in have}
            if new:
                have.update(new)
                owned.save(have)
                print(f"보유 오퍼레이터 {len(new)}명 추가 기록 (모두 {len(have)}명)", flush=True)
        if "养成材料" in texts and texts & {"全部", "消耗物品", "基础物品"}:
            if self.depot is None:
                self.depot = depot.Depot()
            if self.depot.T is None:
                return
            found = dict(self.depot.read(arr, self.ocr))
            if found:
                inv = depot.load()
                changed = {k: v for k, v in found.items() if inv.get(k) != v}
                if changed:
                    inv.update(found)
                    depot.save(inv)
                    print(f"창고 {len(changed)}종 기록 (모두 {len(inv)}종)", flush=True)

    def note_missing(self, text, screen):
        """번역하지 못한 중국어 줄을 overlay_missing.tsv에 남긴다 (빠진 번역·OCR 오독을 모아 고치는 데 씀)."""
        key = normalize(text)
        if len(HAN.findall(key)) < 2 or key in self.missing:
            return
        self.missing.add(key)
        with open(MISSING, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{screen or '-'}\t{text}\n")

    def recruit_panel(self, result, w, h):
        """공개모집 태그 고르기 창이면 4★ 이상 확정 조합 패널 항목 (태그 무리 바로 아래, 자리가 없으면 위)."""
        if not self.recruit:
            return None
        texts = [normalize(text) for _, text, _ in result]
        ids = self.recruit.tag_ids(texts)
        # 태그 고르기 창에만 있는 '招募时限'이 보여야 한다: 모집 목록 화면에도 칸마다 고른 태그가 보여서,
        # 여러 칸의 태그를 모아 엉뚱한 조합('감속 + 강제이동 → 5★')을 냈음
        if len(ids) < 3 or not any("招募时限" in t for t in texts):
            return None
        boxes = [b for b, text, _ in result if normalize(text) in self.recruit.by_zh]
        xs = [p[0] for b in boxes for p in b]
        ys = [p[1] for b in boxes for p in b]
        tag_box = (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))
        line_h = int(np.median([max(p[1] for p in b) - min(p[1] for p in b) for b in boxes]))
        self.owned.refresh()
        rows = self.recruit.describe(ids, owned=self.owned or None)
        return {"key": "recruit", "how": "recruit", "box": tag_box, "line_h": line_h, "vertical": False,
                "zh": "|".join(self.recruit.tags[i]["zh"] for i in ids), "full_zh": "",
                "ko": "\n".join(t for t, _ in rows), "rows": rows, "title": "공개모집 확정 조합 (9시간)",
                "screen": (w, h), "avail": (w - 20, h), "bg": (18, 20, 26), "fg": None}


class Recorder:
    """기록 모드: 화면에 보이는 글자 구성이 바뀔 때마다 게임 화면을 저장 (오프라인 점검용).

    게임 창 영역만, 게임이 맨 앞에 있을 때만 저장한다.
    """

    def __init__(self, folder, min_gap=1.5, limit=2000):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.min_gap, self.limit = min_gap, limit
        self.count = len(list(self.folder.glob("frame_*.png")))
        self.last_sig, self.last_time = None, 0.0

    def save(self, img, lines):
        sig = frozenset(normalize(m["text"]) for m in lines)
        now = time.time()
        if sig == self.last_sig or now - self.last_time < self.min_gap or self.count >= self.limit:
            return
        self.last_sig, self.last_time = sig, now
        self.count += 1
        img.save(self.folder / f"frame_{self.count:04d}.png")
        print(f"기록 {self.count}장", flush=True)


class Overlay:
    def __init__(self, game, matcher, record_dir=None, snapshot_dir=None, always=False, show_in_capture=False):
        self.game = game
        self.capture_note = None  # 방송·화면 공유에 보이기 상태 (트레이에 표시)
        self.notify = None        # 상태가 바뀌면 부를 함수 (트레이 앱이 넣음)
        self.always = always
        self.snapshot_dir = Path(snapshot_dir) if snapshot_dir else None
        if self.snapshot_dir:
            self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.last_img, self.last_snapshot, self.snapshot_count, self.frame = None, 0.0, 0, None
        if self.snapshot_dir:  # 같은 폴더로 다시 켜도 앞 실행의 스냅샷을 덮어쓰지 않게 이어서 번호를 붙임
            self.snapshot_count = len(list(self.snapshot_dir.glob("snap_*.png")))
        self.snapshot_pending = False
        # Tk 창은 처음 뜰 때 입력 포커스를 가져가므로, 뜬 뒤 원래 앞에 있던 창(보통 게임)에 돌려준다
        before = win32gui.GetForegroundWindow()
        # Tk 창은 창 틀(클릭 통과·캡처 제외)과 시간 맞추기(after)에만 쓰고, 내용은 render가 그린 그림을 통째로 올린다
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.update_idletasks()
        self._make_click_through()
        self.layered = render.LayeredWindow(self.hwnd)
        self.push_broken = False  # 레이어드 창에 그림 올리기가 실패하는 중 (창을 숨겨 둠)
        self.root.update()
        if before and win32gui.IsWindow(before) and win32gui.GetForegroundWindow() in (self.hwnd, int(self.root.winfo_id())):
            try:
                win32gui.SetForegroundWindow(before)
            except win32gui.error:
                pass

        # 그리는 방식: natural(원문을 지우고 한국어판처럼) / box(배경색 상자로 덮기). 트레이 메뉴에서 바꿈
        render.STYLE = load_settings().get("render_style", "natural")
        self.events = queue.Queue()
        self.worker = Worker(game, matcher, self.events)
        self.worker.always = always
        if record_dir:
            self.worker.recorder = Recorder(record_dir)
        self.worker.start()
        if show_in_capture:
            self.set_show_in_capture(True)
        self.visible = True
        self.stop = threading.Event()  # 밖(트레이 앱)에서 끝낼 때
        self.exit_reason = None        # 'F10' / '게임 종료' / '종료 요청'
        self.geometry = None
        self.cover = None  # 게임 화면을 덮은 다른 창의 자리 (게임 화면 좌표)
        self.shown_items = {}  # 상자 id → {"item": ..., "misses": n, "born": 처음 그린 시각}
        self.next_id = 0
        self.fading = False  # 서서히 나타나는 상자가 있어 곧 다시 그려야 함
        self.log = open(LOG, "a", encoding="utf-8")
        self.trace_log = open(LOG.with_name("overlay_trace.txt"), "w", encoding="utf-8")
        self.root.after(30, self.tick)

    def _make_click_through(self):
        self.hwnd = hwnd = int(self.root.wm_frame(), 16)
        self.shown = True
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        style |= (win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT | win32con.WS_EX_TOOLWINDOW
                  | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TOPMOST)
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, style)
        if not user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE):
            print("경고: 오버레이를 화면 캡처에서 제외하지 못했습니다. 번역 상자가 다시 인식될 수 있습니다.")

    def set_show_in_capture(self, on):
        """방송·화면 공유(OBS, 디스코드)에 번역 상자가 보이게 할지. 켜면 일꾼이 게임 창만 찍기를 시험한 뒤 알려 준다."""
        if on:
            self.capture_note = "확인 중"
            self.worker.want_window_capture = True
        else:
            self._set_excluded(True)  # 먼저 캡처에서 빼고 나서 화면 복사로 돌아간다
            self.worker.want_window_capture = False
            self.worker.window_capture = False
            self.capture_note = None
        if self.notify:
            self.notify(self.capture_note)

    def _set_excluded(self, excluded):
        ok = user32.SetWindowDisplayAffinity(self.hwnd, WDA_EXCLUDEFROMCAPTURE if excluded else WDA_NONE)
        if excluded:
            self.worker.excluded.set()
        print(f"화면 캡처에서 {'제외' if excluded else '보임'}{'' if ok else ' (설정 실패)'}", flush=True)

    def toggle(self):
        """번역 표시 켜기/끄기 (끄면 인식도 멈춤)."""
        self.visible = not self.visible
        self.worker.enabled = self.visible
        self.shown_items.clear()
        self.redraw()
        print("표시 켬" if self.visible else "표시 끔", flush=True)

    def tick(self):
        if key_pressed(VK_F10):
            self.exit_reason = "F10"
        elif not win32gui.IsWindow(self.game):
            self.exit_reason = "게임 종료"
        elif self.stop.is_set():
            self.exit_reason = "종료 요청"
        if self.exit_reason:
            self.worker.running = False
            self.worker.vocab.save()
            self.log.close()
            self.trace_log.close()
            self.root.destroy()
            return
        if key_pressed(VK_F9):
            self.toggle()

        # 게임이 맨 앞이 아니면 오버레이를 숨김 (보일 때도 포커스는 뺏지 않음)
        show = self.visible and game_active(self.game, self.always) and not self.push_broken
        if show != self.shown:
            win32gui.ShowWindow(self.hwnd, win32con.SW_SHOWNOACTIVATE if show else win32con.SW_HIDE)
            self.shown = show

        changed = False
        # 다른 프로그램 창이 게임 화면 일부를 덮으면 그 창과 겹치는 상자는 그리지 않는다
        cover = foreground_cover(self.game) if show and self.always else None
        cover = cover[0] if cover else None
        if cover != self.cover:
            self.cover = cover
            changed = True
        while not self.events.empty():
            kind, payload = self.events.get_nowait()
            if kind == "toggle":
                self.toggle()
            elif kind == "record":
                self.worker.recorder = Recorder(payload) if payload else None
                print(f"점검 기록 {'켬: ' + str(payload) if payload else '끔'}", flush=True)
            elif kind == "show_in_capture":
                self.set_show_in_capture(payload)
            elif kind == "style":
                # 그리는 방식을 바꾸면 보이던 상자는 지우고 다음 인식 결과부터 새 방식으로 (원문 지운 조각을 새로 만듦)
                render.STYLE = payload
                changed |= bool(self.shown_items)
                self.shown_items.clear()
                self.worker.last_thumb = None
                print(f"그리는 방식: {payload}", flush=True)
            elif kind == "capture_mode":
                ok, err = payload
                self._set_excluded(not ok)
                self.capture_note = "켜짐" if ok else f"안 됨: {err}"
                print(f"방송·화면 공유에 보이기: {self.capture_note}", flush=True)
                if self.notify:
                    self.notify(self.capture_note)
            elif kind == "clear":
                if self.shown_items:
                    self.trace(f"모두 지움(화면 전환) {len(self.shown_items)}개")
                changed |= bool(self.shown_items)
                self.shown_items.clear()
            elif self.visible:
                changed |= self.update_items(*payload)
        if changed:
            self.redraw()
        self.root.after(30, self.tick)

    def update_items(self, geometry, items, ms, img=None, animated=None):
        """새 인식 결과를 화면의 상자들과 합친다. 바뀐 게 있으면 True.

        animated: 축소 화면 크기의 '늘 움직이는 부분' 표시 (Worker.motion). 그 위의 상자는 화면 비교로 지우지 않는다.
        """
        self.last_img = img
        arr = np.asarray(img) if img is not None else None
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY) if arr is not None else None
        changed = False
        if geometry != self.geometry:
            x, y, w, h = geometry
            self.root.geometry(f"{w}x{h}+{x}+{y}")
            self.geometry = geometry
            changed = True
        seen = set()
        for it in items:
            # 같은 문구가 여러 곳에 있을 수 있으므로 (목록의 '行动后' 등) 문구 + 가장 가까운 위치로 짝짓는다
            sid = self._match_shown(it, seen)
            cur = self.shown_items.get(sid)
            if cur is None:
                # 짧은 글자는 같은 자리에서 다른 번역이 두 번 연속 나와야 바꾼다 (OCR이 한 번 잘못 읽은 것으로 흔들리지 않게)
                old_sid = self._short_rival(it, seen)
                if old_sid is not None:
                    old = self.shown_items[old_sid]
                    if old.get("pending") == it["ko"]:
                        old["pending_n"] += 1
                    else:
                        old["pending"], old["pending_n"] = it["ko"], 1
                    if old["pending_n"] < 2:
                        seen.add(old_sid)
                        old["misses"] = 0
                        continue
            seen.add(sid)
            if cur is None:
                # 같은 자리의 상자를 바꾸는 것이면(숫자가 붙었다 떨어졌다 하는 '剩余可放置角色：7') 서서히 나타나지 않고 바로
                replaces = any(overlap_small(e["item"]["box"], it["box"]) >= 0.6 for e in self.shown_items.values())
                if arr is not None:
                    render.attach_patch(it, arr)  # 자연스럽게 그리기: 이 화면에서 원문을 지운 배경 조각
                self.shown_items[sid] = {"item": it, "misses": 0, "born": 0.0 if replaces else time.time(),
                                         "crop": crop_of(gray, it["box"]) if gray is not None else None}
                self.log.write(f"[{it['how']}] {it['zh']}\t→ {it['ko']}\n")
                self.trace(f"새로 {it['zh'][:20]} {it['box']}")
                changed = True
                continue
            cur["misses"] = 0
            cur["pending"] = None  # 원래 번역이 다시 읽혔으므로 바꾸려던 후보는 버림
            # 번역이 같고 상자가 대부분 겹치면 처음 상자를 그대로 둔다. OCR이 옆 아이콘을 넣었다 뺐다 하며
            # 상자 크기가 바뀌어도(창고 위 '⏰5天' / '5天') 번역 상자가 흔들리지 않게
            # (긴 글인 말풍선·대사는 줄 합치기 결과에 따라 상자가 커질 수 있으므로 매번 새 상자를 따름)
            a, b = cur["item"]["box"], it["box"]
            same_place = iou(a, b) >= SAME_PLACE_IOU or covered_ratio(a, b) >= 0.7 or covered_ratio(b, a) >= 0.7
            if cur["item"]["ko"] == it["ko"] and same_place and len(it["zh"]) <= SHORT_TEXT:
                continue
            moved = max(abs(a - b) for a, b in zip(cur["item"]["box"], it["box"]))
            resized = abs(cur["item"]["line_h"] - it["line_h"]) > 3 or cur["item"].get("vertical") != it.get("vertical")
            if moved > MOVE_TOLERANCE or resized or cur["item"]["ko"] != it["ko"]:
                why = "번역" if cur["item"]["ko"] != it["ko"] else ("크기" if resized else f"이동{moved:.0f}")
                self.trace(f"바뀜({why}) {cur['item']['zh'][:20]} {cur['item']['box']} → {it['zh'][:20]} {it['box']}")
                if arr is not None:
                    render.attach_patch(it, arr)
                cur["item"] = it
                cur["crop"] = crop_of(gray, it["box"]) if gray is not None else None
                changed = True
        # 공개모집 패널은 원문 글자가 아니라 태그 무리 자리를 가리키므로 다른 상자를 지우는 판단에 쓰지 않는다
        seen_boxes = [self.shown_items[s]["item"]["box"] for s in seen if self.shown_items[s]["item"]["how"] not in render.PANELS]
        seen_ko = {self.shown_items[s]["item"]["ko"] for s in seen}
        now = time.time()
        for key in list(self.shown_items):
            entry = self.shown_items[key]
            if key in seen:
                entry["unseen_since"] = None
                continue
            # 새로 그린 상자가 옛 상자 자리를 대부분 덮거나 같은 번역이 다른 자리에 새로 나왔으면 옛 상자는 바로 지운다
            # (대사가 바뀌었을 때 잔상 방지). 살짝 닿은 이웃 상자('5天' 아래 '仓库')는 해당 없음.
            # 같은 번역 비교는 긴 글만: 짧은 이름은 한 화면에 여럿 있을 수 있다 (기반시설의 숙소 4개를 OCR이 하나 놓칠 때마다 지워졌음)
            moved_text = len(entry["item"]["zh"]) > SHORT_TEXT and entry["item"]["ko"] in seen_ko
            if any(covered_ratio(entry["item"]["box"], b) >= 0.5 for b in seen_boxes) or moved_text:
                self.trace(f"지움(교체) {entry['item']['zh'][:20]} {entry['item']['box']}")
                del self.shown_items[key]
                changed = True
                continue
            # 이번에 읽은 글자가 옛 상자에 조금이라도 걸치면(이벤트 설명 위로 나온 선택지, 다음 화면의 설명문),
            # 옛 글자 그림이 그 자리에 그대로 보일 때만 남긴다. 움직이는 자리라고 몇 초씩 기다리면 새 글자와 겹쳐 보임
            overlapped = any(overlap_small(entry["item"]["box"], b) >= OVERLAP_REPLACE for b in seen_boxes)
            entry["unseen_since"] = entry.get("unseen_since") or now
            # OCR이 이번에 못 읽었으면, 처음 그릴 때 저장한 원문 글자 그림을 주변에서 찾아 본다.
            # 찾으면 글자가 그대로 있는 것(메뉴판이 몇 픽셀 움직였으면 번역 상자도 따라 옮김), 전혀 다르면 바로 지움
            score = None
            if gray is not None and entry.get("crop") is not None:
                score, dx, dy = find_crop(gray, entry["item"]["box"], entry["crop"])
                if score >= MATCH_SAME and blurred(gray, entry["item"]["box"], entry["crop"], dx, dy):
                    # 창이 열리며 뒤 화면이 흐려진 것 (공개모집 태그 창 뒤의 '모집 중지'): 모양은 닮아도 글자는 가려졌음
                    score = 0.0
                if score >= MATCH_SAME:
                    if dx or dy:
                        x0, y0, x1, y1 = entry["item"]["box"]
                        entry["item"] = {**entry["item"], "box": (x0 + dx, y0 + dy, x1 + dx, y1 + dy)}
                        changed = True
                    continue
            # 그림으로 확인할 수 없거나(밋밋한 자리, Live2D 위 말풍선) 애매하면 OCR만 믿고 KEEP_UNSEEN초까지 둔다
            animated_place = animated is not None and self._animated_ratio(entry["item"]["box"], animated) > MOTION_ITEM
            if not animated_place:
                entry["misses"] += 1
            gone = score is not None and score < MATCH_GONE and not animated_place
            unsure_twice = score is not None and entry["misses"] >= MISSES_TO_REMOVE
            if overlapped or gone or unsure_twice or now - entry["unseen_since"] >= KEEP_UNSEEN:
                sc = "-" if score is None else f"{score:.2f}"
                why = "새 글자와 겹침" if overlapped else f"못 읽음 {entry['misses']}회"
                self.trace(f"지움({why}, 글자 그림 {sc}) {entry['item']['zh'][:20]}")
                del self.shown_items[key]
                changed = True
        changed |= self._drop_nested()
        if changed:
            self.log.flush()
            print(f"인식+매칭 {ms:4.0f}ms, 표시 중 {len(self.shown_items)}개", flush=True)
        return changed

    def _drop_nested(self):
        """같은 글을 두 상자가 겹쳐 보이면 오래된 쪽을 지운다. 바뀐 게 있으면 True.

        OCR이 '04:01:23 联络中'을 한 줄로 읽었다가 시각과 '联络中'을 따로 읽기도 해서, 두 번역이 겹쳐 '서칭 서칭 중'으로 보였음.
        한 상자가 다른 상자 안에 대부분 들어 있고 원문도 그 일부일 때만 해당.
        """
        changed = False
        entries = sorted(self.shown_items.items(), key=lambda kv: kv[1].get("born", 0))
        for key, entry in entries:
            a = entry["item"]
            if key not in self.shown_items or a["how"] in render.PANELS or not a["ko"]:
                continue
            za = normalize(a["zh"])
            for other_key, other in self.shown_items.items():
                b = other["item"]
                if other_key == key or b["how"] in render.PANELS or not b["ko"]:
                    continue
                zb = normalize(b["zh"])
                small, big = (a, b) if len(za) <= len(zb) else (b, a)
                if covered_ratio(small["box"], big["box"]) >= 0.6 and za != zb and \
                        normalize(small["zh"]) in normalize(big["zh"]):
                    # entries가 오래된 순이므로 지금 보는 쪽(key)이 더 오래됨
                    self.trace(f"지움(겹친 같은 글) {a['zh'][:20]} ↔ {b['zh'][:20]}")
                    del self.shown_items[key]
                    changed = True
                    break
        return changed

    def trace(self, msg):
        """상자가 바뀌거나 지워진 이유 (깜빡임 원인 점검용, overlay_trace.txt)."""
        self.trace_log.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
        self.trace_log.flush()

    def _short_rival(self, it, used):
        """같은 자리(많이 겹침)에 이미 보이는 다른 번역의 짧은 글자 상자 id. 없으면 None."""
        if len(it["zh"]) > SHORT_TEXT:
            return None
        for sid, entry in self.shown_items.items():
            old = entry["item"]
            if (sid not in used and old["ko"] != it["ko"] and len(old["zh"]) <= SHORT_TEXT
                    and iou(old["box"], it["box"]) >= SAME_PLACE_IOU):
                return sid
        return None

    def _animated_ratio(self, box, animated):
        """상자 자리 중 '늘 움직이는 부분'의 비율."""
        if not self.geometry:
            return 0.0
        _, _, w, h = self.geometry
        th, tw = animated.shape
        x0, y0 = int(box[0] * tw / w), int(box[1] * th / h)
        x1, y1 = max(x0 + 1, int(box[2] * tw / w)), max(y0 + 1, int(box[3] * th / h))
        region = animated[y0:y1, x0:x1]
        return float(region.mean()) if region.size else 0.0

    def _match_shown(self, it, used, max_dist=60):
        """새 항목과 짝이 되는 기존 상자의 id. 없으면 새 id.

        - 같은 문구이면서 가장 가까운 상자, 또는
        - 번역문이 같고 위치가 겹치는 상자 (대사가 한 글자씩 나오며 원문은 길어져도 번역은 같음)
        """
        cx, cy = (it["box"][0] + it["box"][2]) / 2, (it["box"][1] + it["box"][3]) / 2
        best, best_d = None, max_dist
        for sid, entry in self.shown_items.items():
            if sid in used:
                continue
            old = entry["item"]
            b = old["box"]
            if old["key"] == it["key"]:
                d = abs((b[0] + b[2]) / 2 - cx) + abs((b[1] + b[3]) / 2 - cy)
            elif old["ko"] == it["ko"] and boxes_overlap(b, it["box"]):
                d = 0
            else:
                continue
            if d < best_d:
                best, best_d = sid, d
        if best is not None:
            return best
        self.next_id += 1
        return self.next_id

    def redraw(self):
        """보이는 상자들을 한 장의 그림으로 그려 창에 올린다. 서서히 나타나는 중이면 잠시 뒤 다시 그린다."""
        if not self.geometry:
            return
        x, y, w, h = self.geometry
        # 큰 상자부터 그려서 옆의 작은 글자('오퍼레이터 관리')가 큰 상자 바탕에 덮이지 않게 하고, 공개모집 패널은 맨 위에
        def order(entry):
            x0, y0, x1, y1 = entry["item"]["box"]
            return (entry["item"]["how"] in render.PANELS, -(x1 - x0) * (y1 - y0))

        now, layers, fading = time.time(), [], False
        for entry in sorted(self.shown_items.values(), key=order):
            if self.cover and boxes_overlap(entry["item"]["box"], self.cover):
                continue
            # 상자 그림은 항목이 바뀔 때만 새로 그린다 (서서히 나타나는 동안 여러 번 올려도 가볍게)
            if entry.get("layer_for") is not entry["item"]:
                entry["layers"], entry["layer_for"] = render.item_layers(entry["item"]), entry["item"]
            alpha = min(1.0, (now - entry.get("born", 0)) / FADE_IN)
            fading |= alpha < 1
            layers += [(z, img, pos, alpha) for img, pos, z in entry["layers"]]
        # 원문 지우기(층 0)를 모두 먼저, 그 위에 글자(층 1), 정보 패널(층 2)
        layers.sort(key=lambda l: l[0])
        self.frame = render.compose((w, h), [(img, pos, a) for _, img, pos, a in layers])
        ok = self.layered.push(self.frame, x, y)
        if ok != (not self.push_broken):
            # 그림을 못 올리면 창이 회색 판으로 게임을 가리므로 숨긴다 (다음에 성공하면 다시 보임)
            self.push_broken = not ok
            print("번역 창에 그림을 못 올려 숨김" if self.push_broken else "번역 창 그리기 다시 됨", flush=True)
            if self.push_broken:
                win32gui.ShowWindow(self.hwnd, win32con.SW_HIDE)
                self.shown = False
        if fading and not self.fading:
            self.fading = True
            self.root.after(30, self._fade_step)
        if self.snapshot_dir and self.last_img is not None:
            self.save_snapshot()

    def _fade_step(self):
        self.fading = False
        self.redraw()

    def save_snapshot(self):
        """오버레이가 그린 그대로를 게임 화면 위에 합성해 저장 (오버레이 창은 화면 캡처에 안 잡히므로 점검용)."""
        now = time.time()
        if now - self.last_snapshot < 0.5:
            # 너무 자주 저장하지 않되, 마지막 상태는 잠시 뒤 꼭 저장한다
            if not self.snapshot_pending:
                self.snapshot_pending = True
                self.root.after(600, self.save_snapshot)
            return
        self.snapshot_pending = False
        self.last_snapshot = now
        img = self.last_img.convert("RGBA")
        if self.frame is not None and self.frame.size == img.size:
            img.alpha_composite(self.frame)
        img = img.convert("RGB")
        self.snapshot_count += 1
        img.save(self.snapshot_dir / "latest.png")
        if self.snapshot_count <= 2000:
            img.save(self.snapshot_dir / f"snap_{self.snapshot_count:04d}.png")

    def run(self):
        print("오버레이 시작. F9: 표시 켜기/끄기, F10: 종료", flush=True)
        self.root.mainloop()


def main():
    import argparse

    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("dict_path")
    ap.add_argument("--record", metavar="DIR", help="화면이 바뀔 때마다 게임 화면을 DIR에 저장 (점검용)")
    ap.add_argument("--always", action="store_true", help="게임이 맨 앞이 아니어도 동작 (게임을 다른 모니터에 띄웠을 때)")
    ap.add_argument("--snapshot", metavar="DIR", help="오버레이가 그린 결과를 게임 화면에 합성해 DIR에 저장 (점검용)")
    args = ap.parse_args()

    game = find_game_window()
    if not game:
        sys.exit(f"'{WINDOW_TITLE}' 창을 찾지 못했습니다. 게임을 창 모드로 켜 주세요.")
    t = time.perf_counter()
    matcher = Matcher(args.dict_path)
    print(f"사전 {len(matcher.keys):,}개 로드 ({time.perf_counter() - t:.1f}초)", flush=True)
    if args.record:
        print(f"기록 모드: {args.record}", flush=True)
    Overlay(game, matcher, args.record, args.snapshot, args.always).run()


if __name__ == "__main__":
    main()
