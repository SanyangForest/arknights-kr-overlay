"""바탕화면에 '명일방주 한국어 오버레이' 바로가기를 만든다 (아이콘 app_icon.ico도 함께).

사용법: python make_shortcut.py
"""

import sys
from pathlib import Path

import win32com.client

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    from PIL import Image, ImageDraw, ImageFont

    # overlay_app.pyw를 import하면 로그 파일을 열어 버리므로 아이콘 그림만 같은 모양으로 그린다
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((1, 1, size - 2, size - 2), radius=size // 5, fill=(28, 44, 78, 255))
    d.text((size / 2, size / 2), "한", font=ImageFont.truetype("C:/Windows/Fonts/malgun.ttf", int(size * 0.6)),
           fill="white", anchor="mm")
    ico = HERE / "app_icon.ico"
    img.save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

    shell = win32com.client.Dispatch("WScript.Shell")
    desktop = Path(shell.SpecialFolders("Desktop"))
    link = shell.CreateShortcut(str(desktop / "명일방주 한국어 오버레이.lnk"))
    link.TargetPath = str(HERE.parent / ".venv" / "Scripts" / "pythonw.exe")
    link.Arguments = f'"{HERE / "overlay_app.pyw"}"'
    link.WorkingDirectory = str(HERE)
    link.IconLocation = str(ico)
    link.Description = "명일방주(중섭) 화면에 한국어 번역을 덮어씌우는 오버레이"
    link.Save()
    print("바로가기:", desktop / "명일방주 한국어 오버레이.lnk")


if __name__ == "__main__":
    main()
