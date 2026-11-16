"""명일방주 한국어 오버레이 앱: 콘솔 창 없이 트레이 아이콘으로 켜고 끈다.

바탕화면 아이콘(make_shortcut.py가 만듦)이나 'pythonw overlay_app.pyw'로 실행한다.
- 게임(明日方舟 창)이 켜질 때까지 기다렸다가 오버레이를 띄우고, 게임을 끄면 다시 기다린다.
- 트레이 아이콘: 왼쪽 클릭 = 번역 표시 켜기/끄기. 오른쪽 클릭 메뉴 = 방송·화면 공유에 보이기, 한국어판처럼 그리기,
  육성 계산기, 점검 기록, 기록 폴더 열기, 종료.
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

import datastore
import paths

HERE = Path(__file__).resolve().parent
paths.USER.mkdir(parents=True, exist_ok=True)
LOG = paths.USER / "overlay_app_log.txt"
sys.stdout = sys.stderr = open(LOG, "w", encoding="utf-8", buffering=1)

import ctypes

import pystray
import win32api
import win32event
import winerror
from PIL import Image, ImageDraw, ImageFont

import overlay
import updater
from matcher import Matcher

DICT = datastore.dict_path()  # 개발: out/dict_zh_ko.json, 배포판: data/pc.db
CAPTURES = paths.CAPTURES
SETTINGS = paths.SETTINGS
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
        self.natural = self.load_settings().get("render_style", "natural") == "natural"
        self.capture_note = None
        self.status = "시작하는 중"
        self.restart = False       # 업데이트 적용을 위해 끝난 뒤 다시 켬
        self.update_note = None    # "사전 판 … 받음 (다시 켜면 적용)" 등
        self.new_app = None        # 새 앱 버전 정보 (update.json pc_app)
        M = pystray.MenuItem
        self.icon = pystray.Icon("ak_kr_overlay", make_icon(), TITLE, menu=pystray.Menu(
            M(lambda item: self.status, None, enabled=False),
            pystray.Menu.SEPARATOR,
            M("번역 표시 (F9)", self.on_toggle, checked=lambda item: self.is_visible(), default=True),
            M(lambda item: "방송·화면 공유에 보이기" + (f" ({self.capture_note})" if self.capture_note else ""),
              self.on_capture, checked=lambda item: self.show_in_capture),
            M("한국어판처럼 그리기 (원문 지우기)", self.on_style, checked=lambda item: self.natural),
            M("육성 계산기 열기", self.on_planner),
            M(lambda item: "박사 이름 설정" + ("" if self.load_settings().get("nickname") else " (안 함)"), self.on_nickname),
            M("점검 기록 (화면 저장)", self.on_record, checked=lambda item: self.record_dir is not None),
            M("기록 폴더 열기", lambda: os.startfile(self.record_dir or CAPTURES)),
            pystray.Menu.SEPARATOR,
            M(lambda item: "업데이트 확인" + (f" ({self.update_note})" if self.update_note else ""), self.on_check_update,
              visible=lambda item: datastore.USE_PCDB),
            M("다시 시작해서 사전 업데이트 적용", self.on_restart, visible=lambda item: updater.PENDING.exists()),
            M(lambda item: f"새 버전 받기 ({self.new_app['version']})" if self.new_app else "", self.on_new_app,
              visible=lambda item: self.new_app is not None),
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

    def on_style(self):
        """그리는 방식: 원문 글자를 지우고 그 자리에 한국어를 씀(한국어판처럼) ↔ 배경색 상자로 덮음 (예전 방식)."""
        self.natural = not self.natural
        style = "natural" if self.natural else "box"
        settings = self.load_settings()
        settings["render_style"] = style
        SETTINGS.write_text(json.dumps(settings, ensure_ascii=False, indent=1), encoding="utf-8")
        if self.overlay:
            self.overlay.events.put(("style", style))

    # ------------------------------------------------------------ 자동 업데이트 (배포판만)

    def update_loop(self):
        """켠 뒤 잠시 있다가, 그 뒤로 6시간마다 업데이트 확인 (settings.json auto_update가 false면 안 함)."""
        if self.quit.wait(20):
            return
        while not self.quit.is_set():
            if self.load_settings().get("auto_update", True):
                self.check_update(quiet=True)
            if self.quit.wait(6 * 3600):
                return

    def check_update(self, quiet=False):
        try:
            r = updater.check(log=lambda m: print(m, flush=True))
        except Exception as e:  # noqa: BLE001 — 인터넷이 없거나 GitHub이 안 되면 다음에
            print(f"업데이트 확인 실패: {e}", flush=True)
            if not quiet:
                self.icon.notify(f"업데이트를 확인하지 못했어요: {e}", TITLE)
            return
        if r["dict"]:
            self.update_note = f"사전 {r['dict']} 받음"
            self.icon.notify("사전 업데이트를 받았어요. 트레이 메뉴 '다시 시작해서 사전 업데이트 적용'이나 다음 실행 때 적용돼요.", TITLE)
        elif not quiet:
            self.update_note = "최신"
            self.icon.notify(f"사전이 최신이에요 (판 {r['current']}).", TITLE)
        if r["app"] and (not self.new_app or self.new_app.get("version") != r["app"].get("version")):
            self.new_app = r["app"]
            self.icon.notify(f"새 버전 {r['app']['version']}이 있어요. 트레이 메뉴 '새 버전 받기'에서 받을 수 있어요.", TITLE)
        self.icon.update_menu()

    def on_check_update(self):
        threading.Thread(target=self.check_update, daemon=True).start()

    def on_restart(self):
        self.restart = True
        self.on_quit()

    def on_new_app(self):
        if self.new_app:
            os.startfile(self.new_app.get("page") or self.new_app["url"])

    def on_capture_note(self, note):
        """오버레이가 알려 준 방송·화면 공유 상태. 안 되면 알림으로도 알린다."""
        self.capture_note = note
        self.icon.update_menu()
        if note and note.startswith("안 됨"):
            self.icon.notify(f"방송·화면 공유에 보이기 {note}", TITLE)

    def on_nickname(self):
        """게임 속 박사 이름: 대사에 이름이 들어간 문장('果然○○博士的指挥')을 찾고 번역에 이름을 넣는 데 씀.

        트레이 스레드에서 작은 Tk 입력 창을 띄운다. 바꾸면 다음에 사전을 불러올 때(앱 다시 시작) 반영된다.
        """
        def ask():
            import tkinter as tk
            from tkinter import simpledialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            cur = self.load_settings().get("nickname") or ""
            name = simpledialog.askstring(TITLE, "게임 속 박사 이름 (Dr. 뒤의 이름, 비우면 안 씀):", initialvalue=cur, parent=root)
            root.destroy()
            if name is None:
                return
            settings = self.load_settings()
            settings["nickname"] = name.strip()
            SETTINGS.write_text(json.dumps(settings, ensure_ascii=False, indent=1), encoding="utf-8")
            self.icon.notify("박사 이름을 저장했어요. 앱을 다시 켜면 반영돼요.", TITLE)
        threading.Thread(target=ask, daemon=True).start()

    def on_planner(self):
        """창고·보유 오퍼레이터·드롭 통계로 육성 계산기 페이지를 만들어 브라우저로 연다 (planner.py)."""
        try:
            import planner
            planner.open_planner()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.icon.notify(f"육성 계산기를 열지 못했어요: {e}", TITLE)

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
        if datastore.USE_PCDB:
            threading.Thread(target=self.update_loop, daemon=True).start()
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
    # 배포판(exe)은 자기 자신을, 개발에서는 pythonw로 이 파일을 다시 실행
    args = "" if paths.FROZEN else f'"{Path(__file__).resolve()}"'
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, args, str(paths.BASE if paths.FROZEN else HERE), 1)
    return rc > 32


def selftest(images):
    """게임 없이 점검: 그림 파일들을 오버레이와 같은 순서로 읽어 번역 결과를 user/selftest.txt에 적는다.

    배포판에서 OCR(DirectML)·사전·스토리 대본이 제대로 붙는지 확인하는 용도 (ak-kr-overlay.exe --selftest 그림.png).
    """
    import queue
    import numpy as np
    out = paths.USER / "selftest.txt"
    with open(out, "w", encoding="utf-8") as f:
        t = time.perf_counter()
        matcher = Matcher(str(DICT))
        f.write(f"사전 {len(matcher.keys):,}개 ({time.perf_counter() - t:.1f}초)\n")
        worker = overlay.Worker(None, matcher, queue.Queue(), offline=True)
        f.write(f"OCR 장치: {overlay.ocr_engine_providers(worker)}\n스토리 대본: {'있음' if worker.story else '없음'}\n")
        for path in images:
            img = Image.open(path).convert("RGB")
            t = time.perf_counter()
            items, _ = worker.analyze(np.asarray(img), *img.size)
            f.write(f"\n{path}: 상자 {len(items)}개, {(time.perf_counter() - t) * 1000:.0f}ms\n")
            for it in items:
                f.write(f"  [{it['how']}] {it['zh'][:30]} → {it['ko'][:50]}\n")
    if "--quiet" not in sys.argv:
        message(f"점검 결과를 저장했어요:\n{out}")


def main():
    if "--selftest" in sys.argv:
        selftest([a for a in sys.argv[1:] if not a.startswith("--")])
        return
    if "--update-check" in sys.argv:
        # 점검용: 업데이트를 확인·받고 바로 적용한 뒤 결과를 user/update_check.txt에 (앱은 켜지 않음)
        with open(paths.USER / "update_check.txt", "w", encoding="utf-8") as f:
            try:
                r = updater.check(log=lambda m: f.write(m + "\n"))
                f.write(f"결과: 사전 {r['dict']}, 지금 판 {r['current']}, 새 앱 {r['app'] and r['app'].get('version')}\n")
                f.write(f"적용: {updater.apply_pending(log=lambda m: f.write(m + chr(10)))}\n")
            except Exception as e:  # noqa: BLE001
                f.write(f"실패: {e!r}\n")
        return
    if relaunch_as_admin():
        return
    # 두 번 켜면 오버레이가 겹쳐 그려지므로 하나만
    mutex = win32event.CreateMutex(None, False, "ak_kr_overlay_app")
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        message("이미 실행 중입니다. 작업 표시줄 오른쪽 트레이의 '한' 아이콘을 확인하세요.")
        return
    app = None
    try:
        if datastore.USE_PCDB:
            updater.apply_pending(log=lambda m: print(m, flush=True))  # 지난번에 받아 둔 사전 (pc.db를 열기 전에)
        app = App()
        app.run()
    except Exception:
        traceback.print_exc()
        message(f"오류로 종료했습니다.\n자세한 내용: {LOG}", error=True)
    finally:
        del mutex
    if app and app.restart:
        # 업데이트 적용: 하나만 켜기 잠금을 푼 뒤 자기 자신을 다시 실행 (관리자 권한이면 그대로 이어받음)
        args = "" if paths.FROZEN else f'"{Path(__file__).resolve()}"'
        ctypes.windll.shell32.ShellExecuteW(None, "open", sys.executable, args, str(paths.BASE if paths.FROZEN else HERE), 1)


if __name__ == "__main__":
    main()
