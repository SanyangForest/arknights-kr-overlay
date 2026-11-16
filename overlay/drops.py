"""스테이지 드롭 정보: 작전 지역에서 고른 스테이지의 주요 드롭과 아이템당 이성을 패널로 보여 준다.

데이터: 펭귄 물류 통계(penguin-stats.io) CN 드롭 행렬 (overlay/data/penguin_cn.json, 하루 한 번 새로 받음)
      + 게임 데이터 stage_table(코드·이름·이성), item_table(아이템 이름·등급, 한국어는 kr 쪽)
선택한 스테이지 찾기: 화면에 스테이지 코드('CE-6')와 그 스테이지 이름('实验原件押运')이 함께 보이면 그 스테이지
  (지도 위 다른 스테이지는 코드만 보임).
"""

import json
import re
import threading
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

import datastore
import paths
from matcher import normalize

DATA = paths.USER_DATA / "penguin_cn.json"
URL = "https://penguin-stats.io/PenguinStats/api/v2/result/matrix?server=CN"
CODE = re.compile(r"^[A-Z]{0,4}\d{0,2}-?[A-Z]?\d{1,2}$")
MIN_TIMES = 100     # 이보다 적게 집계된 드롭은 믿기 어려워 뺌
# 아이템 등급 색 (1 회색, 2 초록, 3 파랑, 4 보라, 5 노랑)
ITEM_COLOR = {1: (190, 190, 190), 2: (180, 230, 120), 3: (110, 180, 255), 4: (205, 170, 255), 5: (255, 208, 64)}


def refresh(max_age_hours=24):
    """드롭 행렬이 없거나 오래됐으면 새로 받는다 (실패해도 있던 파일로 계속)."""
    if DATA.exists() and time.time() - DATA.stat().st_mtime < max_age_hours * 3600:
        return
    try:
        DATA.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(URL, timeout=30) as r:
            body = r.read()
        json.loads(body)
        tmp = DATA.with_suffix(".tmp")
        tmp.write_bytes(body)
        tmp.replace(DATA)
        print("드롭 통계 새로 받음", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"드롭 통계를 받지 못함: {e}", flush=True)


class Drops:
    def __init__(self):
        stages = datastore.excel("stage_table")["stages"]
        items = datastore.excel("item_table")["items"]
        kr_items = datastore.excel("item_table", "kr")["items"]
        self.items = {k: (kr_items.get(k, v)["name"], int(str(v.get("rarity", "TIER_1")).split("_")[-1]))
                      for k, v in items.items()}
        self.kinds = {k: v.get("itemType") for k, v in items.items()}
        self.by_code = defaultdict(list)  # 코드 → [(stageId, 이름(정규화), 이성)]
        for sid, s in stages.items():
            if s.get("code") and s.get("apCost"):
                self.by_code[s["code"]].append((sid, normalize(s.get("name") or ""), s["apCost"]))
        self.matrix = defaultdict(list)
        if DATA.exists():
            self.load()

    def load(self):
        """지금 집계 중인(end 없음) 재료·작전기록 드롭만. 끝난 기간의 줄은 이벤트 보급품 등이 섞여 뺌."""
        self.matrix = defaultdict(list)
        for e in json.loads(DATA.read_text(encoding="utf-8"))["matrix"]:
            iid = e["itemId"]
            if (e.get("end") is None and e["times"] >= MIN_TIMES and e["quantity"] > 0
                    and self.kinds.get(iid) in ("MATERIAL", "CARD_EXP")):
                self.matrix[e["stageId"]].append((iid, e["quantity"] / e["times"]))

    def selected(self, result):
        """화면에서 고른 스테이지: (stageId, 코드, 이성, 코드 상자) 또는 None."""
        lines = [(box, normalize(text)) for box, text, _ in result]
        names = {t for _, t in lines}
        found = []
        for box, t in lines:
            code = t.upper()
            if CODE.match(code) and code in self.by_code:
                for sid, name, ap in self.by_code[code]:
                    if name and name in names and sid in self.matrix:
                        ys = [p[1] for p in box]
                        found.append((min(ys), sid, code, ap, box))
        if not found:
            return None
        _y, sid, code, ap, box = min(found)  # 오른쪽 위 상세 칸이 가장 위
        return sid, code, ap, box

    def rows(self, sid, ap, limit=6):
        """[(글, 색)] 아이템당 이성 함께."""
        # 재료를 확률 높은 순으로, 작전기록은 뒤로
        drops = sorted(self.matrix.get(sid, []), key=lambda d: (self.kinds.get(d[0]) == "CARD_EXP", -d[1]))
        out = []
        for iid, rate in drops[:limit]:
            name, rarity = self.items[iid]
            per = ap / rate if rate > 0 else 0
            out.append((f"{name}  {rate * 100:.1f}%  · 개당 이성 {per:.1f}", ITEM_COLOR.get(rarity, (220, 220, 220))))
        return out

    def panel(self, result, w, h):
        """작전 지역 화면이면 드롭 패널 항목 (오버레이 Worker가 그리는 형식), 아니면 None."""
        sel = self.selected(result)
        if not sel:
            return None
        sid, code, ap, box = sel
        rows = self.rows(sid, ap)
        if not rows:
            return None
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        anchor = (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))
        return {"key": "drops", "how": "panel", "place": "left", "box": anchor,
                "line_h": max(20, anchor[3] - anchor[1]), "vertical": False, "zh": f"drops:{sid}", "full_zh": "",
                "ko": "\n".join(t for t, _ in rows), "rows": rows,
                "title": f"{code} 드롭 (펭귄 통계, 이성 {ap})", "screen": (w, h), "avail": (w - 20, h),
                "bg": (18, 20, 26), "fg": None}


def start_refresh_thread():
    threading.Thread(target=refresh, daemon=True).start()
