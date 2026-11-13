@echo off
rem 명일방주 한국어 오버레이 실행 (F9: 표시 켜기/끄기, F10: 종료)
rem 게임을 다른 모니터에 띄워도 동작하도록 --always 로 실행합니다.
cd /d "%~dp0overlay"
"%~dp0.venv\Scripts\python.exe" overlay.py "%~dp0out\dict_zh_ko.json" --always
pause
