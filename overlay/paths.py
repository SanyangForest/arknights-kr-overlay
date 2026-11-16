"""파일 위치를 한곳에서 정한다. 개발(저장소 그대로 실행)과 배포판(PyInstaller로 묶은 폴더, build_pc.py)이 다르다.

배포판 폴더:
  명방한글오버레이.exe
  _internal/app/  코드와 함께 쓰는 읽기 전용 파일 (screens.json, ocr_aliases.tsv, marquee.tsv, templates/, fonts/ …)
  data/           사전·스토리 대본·게임 표 (dict_zh_ko.json, story.db, recruit.json, gamedata/cn|kr/gamedata/excel/*.json …)
  models/         OCR 모델 (current.json, akkr-*/)
  user/           설정·기록 (settings.json, 로그, captures/, data/ = 보유 오퍼레이터·창고·드롭 통계·낱말장)
개발에서는 예전 자리 그대로: overlay/, out/, data/gamedata/, tools/ocr_models/.
"""

import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:
    BASE = Path(sys.executable).resolve().parent
    CODE = Path(getattr(sys, "_MEIPASS", BASE)) / "app"
    OUT = BASE / "data"
    GAME = BASE / "data" / "gamedata"
    MODELS = BASE / "models"
    USER = BASE / "user"
    MAA_ITEMS = BASE / "maa_items"  # 배포판에는 넣지 않음 (MAA 템플릿은 AGPL): 없으면 창고 인식만 꺼짐
else:
    CODE = Path(__file__).resolve().parent
    BASE = CODE.parent
    OUT = BASE / "out"
    GAME = BASE / "data" / "gamedata"
    MODELS = BASE / "tools" / "ocr_models"
    USER = CODE
    MAA_ITEMS = BASE / "tools" / "maa_templates" / "resource" / "template" / "items"

USER_DATA = USER / "data"   # 보유 오퍼레이터·창고·드롭 통계·낱말장·육성 계산기 페이지
SETTINGS = USER / "settings.json"
CAPTURES = USER / "captures"


def excel(name, region="cn"):
    """게임 표 파일 (character_table 등)."""
    return GAME / region / "gamedata" / "excel" / f"{name}.json"
