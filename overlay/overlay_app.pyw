"""명일방주 한국어 오버레이 앱: 콘솔 창 없이 트레이 아이콘으로 켜고 끈다.

바탕화면 아이콘(make_shortcut.py가 만듦)이나 'pythonw overlay_app.pyw'로 실행한다.
- 게임(明日方舟 창)이 켜질 때까지 기다렸다가 오버레이를 띄우고, 게임을 끄면 다시 기다린다.
- 트레이 아이콘: 왼쪽 클릭 = 번역 표시 켜기/끄기. 오른쪽 클릭 메뉴 = 방송·화면 공유에 보이기, 점검 기록, 기록 폴더 열기, 종료.
  방송·화면 공유에 보이기는 settings.json(show_in_capture)에 저장해 다음 실행에도 쓴다.
- 단축키 F9(표시 켜기/끄기), F10(앱 종료)도 그대로 쓴다.
- 출력과 오류는 overlay_app_log.txt에 남는다 (콘솔 창이 없으므로).
"""

import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = HERE / "overlay_app_log.txt"
sys.stdout = sys.stderr = open(LOG, "w", encoding="utf-8", buffering=1)

import ctypes

import pystray
import win32api
import win32event
import winerror
from PIL import Image, ImageDraw, ImageFont

import overlay
from matcher import Matcher

DICT = HERE.parent / "out" / "dict_zh_ko.json"
CAPTURES = HERE / "captures"
SETTINGS = HERE / "settings.json"
TITLE = "명일방주 한국어 오버레이"


def message(text, error=False):
    ctypes.windll.user32.MessageBoxW(None, text, TITLE, 0x10 if error else 0x40)


def make_icon(size=64):
    """트레이·바탕화면 아이콘: 남색 둥근 사각형에 '한'."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((1, 1, size - 2, size - 2), radius=size // 5, fill=(28, 44, 78, 255))
    font = ImageFont.truetype(overlay.SNAPSHOT_FONT, int(size * 0.6))
    d.text((size / 2, size / 2), "한", font=font, fill="white", anchor="mm")
    return img


class App:
    def __init__(self):
        self.quit = threading.Event()
        self.overlay = None
        self.visible = True
        self.record_dir = None
        self.show_in_capture = bool(self.load_settings().get("show_in_capture"))
        self.capture_note = None
        self.status = "시작하는 중"
        M = pystray.MenuItem
        self.icon = pystray.Icon("ak_kr_overlay", make_icon(), TITLE, menu=pystray.Menu(
            M(lambda item: self.status, None, enabled=False),
            pystray.Menu.SEPARATOR,
            M("번역 표시 (F9)", self.on_toggle, checked=lambda item: self.is_visible(), default=True),
            M(lambda item: "방송·화면 공유에 보이기" + (f" ({self.capture_note})" if self.capture_note else ""),
              self.on_capture, checked=lambda item: self.show_in_capture),
            M("점검 기록 (화면 저장)", self.on_record, checked=lambda item: self.record_dir is not None),
            M("기록 폴더 열기", lambda: os.startfile(self.record_dir or CAPTURES)),
            pystray.Menu.SEPARATOR,
            M("종료 (F10)", self.on_quit),
        ))

    def is_visible(self):
        return self.overlay.visible if self.overlay else self.visible

    def set_status(self, text):
        if text == self.status:
            return
        self.status = text
        self.icon.title = f"{TITLE} - {text}"
        self.icon.update_menu()
        print(text, flush=True)

    # 메뉴 동작은 트레이 스레드에서 불리므로 오버레이(Tk)에는 큐로만 전한다
    def on_toggle(self):
        self.visible = not self.is_visible()
        if self.overlay:
            self.overlay.events.put(("toggle", None))

    @staticmethod
    def load_settings():
        try:
            return json.loads(SETTINGS.read_text(encoding="utf-8-sig"))  # 메모장으로 고치면 BOM이 붙음
        except (OSError, ValueError):
            return {}

    def on_capture(self):
        """OBS·디스코드 화면 공유에 번역 상자가 보이게 (게임 창만 따로 찍어 읽음)."""
        self.show_in_capture = not self.show_in_capture
        settings = self.load_settings()
        settings["show_in_capture"] = self.show_in_capture
        SETTINGS.write_text(json.dumps(settings, ensure_ascii=False, indent=1), encoding="utf-8")
        if self.overlay:
            self.overlay.events.put(("show_in_capture", self.show_in_capture))

    def on_capture_note(self, note):
        """오버레이가 알려 준 방송·화면 공유 상태. 안 되면 알림으로도 알린다."""
        self.capture_note = note
        self.icon.update_menu()
        if note and note.startswith("안 됨"):
            self.icon.notify(f"방송·화면 공유에 보이기 {note}", TITLE)

    def on_record(self):
        self.record_dir = None if self.record_dir else CAPTURES / time.strftime("app_%Y%m%d_%H%M")
        if self.overlay:
            self.overlay.events.put(("record", self.record_dir))

    def on_quit(self):
        self.quit.set()
        if self.overlay:
            self.overlay.stop.set()

    def run(self):
        threading.Thread(target=self.icon.run, daemon=True).start()
        matcher = None
        while not self.quit.is_set():
            game = overlay.find_game_window()
            if not game:
                self.set_status("게임을 기다리는 중")
                self.quit.wait(2)
                continue
            if matcher is None:
                self.set_status("사전 불러오는 중")
                matcher = Matcher(str(DICT))
            # 게임을 다른 모니터에 띄워도 동작하도록 always (run_overlay.bat과 같음)
            ov = overlay.Overlay(game, matcher, self.record_dir, None, always=True, show_in_capture=self.show_in_capture)
            ov.notify = self.on_capture_note
            if not self.visible:
                ov.toggle()
            self.overlay = ov
            self.set_status("번역 중")
            ov.run()  # 게임 종료·F10·트레이 '종료'까지
            self.overlay = None
            print(f"오버레이 끝 ({ov.exit_reason})", flush=True)
            if ov.exit_reason == "F10":
                self.quit.set()
        self.icon.stop()


def relaunch_as_admin():
    """방송·화면 공유에 보이기가 켜져 있으면 관리자 권한으로 다시 실행한다 (UAC 확인).

    게임이 관리자 권한으로 돌아 일반 권한으로는 게임 창을 따로 찍지 못하고, 그러면 오버레이가
    다시 캡처에서 빠져 디스코드·OBS에 안 보인다. 거절하면 False (일반 권한으로 그냥 켬).
    """
    if ctypes.windll.shell32.IsUserAnAdmin() or not App.load_settings().get("show_in_capture"):
        return False
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, f'"{Path(__file__).resolve()}"', str(HERE), 1)
    return rc > 32


def main():
    if relaunch_as_admin():
        return
    # 두 번 켜면 오버레이가 겹쳐 그려지므로 하나만
    mutex = win32event.CreateMutex(None, False, "ak_kr_overlay_app")
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        message("이미 실행 중입니다. 작업 표시줄 오른쪽 트레이의 '한' 아이콘을 확인하세요.")
        return
    try:
        App().run()
    except Exception:
        traceback.print_exc()
        message(f"오류로 종료했습니다.\n자세한 내용: {LOG}", error=True)
    finally:
        del mutex


if __name__ == "__main__":
    main()
