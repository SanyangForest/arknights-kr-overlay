"""스토리 따라가기: 화면 대사로 지금 어느 스토리의 몇 번째 대사인지 찾고, 그 뒤로는 공식 한국어 대본을 순서대로 쓴다.

줄마다 사전에서 찾는 방식(matcher)은 대사가 한 글자씩 나오는 중이거나, 줄바꿈으로 잘리거나, OCR이 한두 글자
틀리면 흔들린다. 스토리는 정해진 순서로 나오므로 위치를 한 번 잡으면 다음에 나올 대사 몇 개만 비교하면 된다.
후보가 적으니 앞 몇 글자만 보여도, 몇 글자 틀려도 어느 대사인지 알 수 있고, 처음부터 대사 전체의 번역을 보여 준다.

대본 색인: out/story.db (build_story.py). 게임 파일·메모리는 건드리지 않고 화면 글자만 쓴다.
"""

import re
import sqlite3
import time
from pathlib import Path

from rapidfuzz import fuzz

import paths
from matcher import HAN, display, normalize

DB = paths.OUT / "story.db"
BACK, AHEAD = 4, 16       # 지금 위치 앞뒤로 비교할 대본 글 수 (건너뛰기·자동 진행으로 몇 줄 넘어가도 따라감)
MIN_LOCK_HAN = 5          # 처음 위치를 잡을 때 쓰는 줄의 최소 한자 수 ('嗯……' 같은 짧은 대사는 여러 스토리에 있음)
MAX_CANDS = 400           # 한 줄로 찾은 후보가 이보다 많으면 그 줄로는 위치를 잡지 않음
NEAR = 12                 # 후보 둘이 같은 파일에서 이만큼 안에 있으면 같은 장면
UNLOCK_AFTER = 30.0       # 스토리 화면도 대본 글도 이만큼 안 보이면 위치를 놓음
TITLE_SHOW = 6.0          # 새 스토리를 잡으면 제목을 이만큼 보여 줌
_EDGE = re.compile(r"^[^\w{\[(<\"']+|[^\w}\])>\"'!?]+$")


def core(s):
    """비교용 모양: matcher.normalize 뒤 앞뒤 문장부호를 뗀다 (OCR은 대사 앞 '……'와 끝 '。'를 자주 빼먹음)."""
    return _EDGE.sub("", normalize(s))


def compare(q, u):
    """화면 줄 q와 대본 글 u: (종류, 점수). full=같은 글, head=앞부분(아직 나오는 중), part=가운데·끝(줄바꿈된 다음 줄)."""
    if q == u:
        return "full", 100
    n = len(q)
    if u.startswith(q):
        return "head", 96
    if n >= 4:
        r = fuzz.ratio(q, u)
        if r >= 84 and abs(len(u) - n) <= max(2, n // 6):
            return "full", r
        if len(u) > n:
            rp = fuzz.ratio(q, u[:n])
            if rp >= 84:
                return "head", rp - 4
    if n >= 2 and q in u:
        return "part", 92
    if n >= 6 and len(u) > n:
        pr = fuzz.partial_ratio(q, u)
        if pr >= 86:
            return "part", pr - 6
    return None, 0


class Story:
    def __init__(self, path=DB, nickname=None):
        self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False) if Path(path).exists() else None
        self.nickname = nickname
        self.file = None          # 지금 스토리 파일 id
        self.idx = -1             # 마지막으로 확인한 대본 글 번호
        self.pending = []         # 위치를 못 정했을 때의 후보 [(file, idx)] — 다음 화면 대사로 좁힌다
        self.ui_at = self.seen_at = 0.0
        self.locked_at = 0.0
        self.boxes = {}           # (file, idx) → 대사 전체가 들어갈 상자 (글자가 나오는 동안 상자 크기를 고정)
        self.info = None          # files 행 (제목)

    def __bool__(self):
        return self.db is not None

    # ------------------------------------------------------------ 색인

    def _rows(self, sql, args):
        return self.db.execute(sql, args).fetchall()

    def _window(self, file, lo, hi):
        return self._rows("SELECT idx, grp, kind, name, name_ko, norm, ko FROM units WHERE file=? AND idx BETWEEN ? AND ?",
                          (file, lo, hi))

    def _find(self, q):
        """q와 같거나(앞부분 포함) 그 글로 시작하는 대본 글 [(file, idx)]. 너무 흔하면 []."""
        rows = self._rows("SELECT file, idx FROM units WHERE norm=? LIMIT ?", (q, MAX_CANDS + 1))
        if not rows and len(q) >= 8:
            rows = self._rows("SELECT file, idx FROM units WHERE norm>=? AND norm<? LIMIT ?", (q, q + "￿", MAX_CANDS + 1))
        return rows if len(rows) <= MAX_CANDS else []

    # ------------------------------------------------------------ 따라가기

    def lock(self, file, idx, why):
        if file != self.file:
            self.info = self.db.execute("SELECT path, code, title, title_ko, tag_ko FROM files WHERE id=?", (file,)).fetchone()
            self.locked_at = time.time()
            self.boxes.clear()
            print(f"스토리 위치 잡음 ({why}): {self.info[0]} #{idx} {self.info[3] or self.info[2]}", flush=True)
        self.file, self.idx, self.pending = file, idx, []

    def unlock(self):
        if self.file is not None:
            print("스토리 위치 놓음", flush=True)
        self.file, self.idx, self.pending, self.info = None, -1, [], None

    def _q(self, text):
        if self.nickname and self.nickname in text:
            text = text.replace(self.nickname, "{@nickname}")
        return core(text)

    def _acquire(self, qs, frame_texts=()):
        """지금 위치 밖의 화면 대사로 새 위치를 찾는다 (처음, 건너뛰기, 다음 스토리). 찾으면 True.

        짧은 대사('你人没事？')는 여러 스토리에 있으므로 같은 화면에 보이는 화자 이름과 함께 찾는다.
        """
        found = []
        names = [t for t in frame_texts if 1 < len(t) <= 8]
        for _i, q in qs:
            n = len(HAN.findall(q))
            if n >= MIN_LOCK_HAN:
                rows = self._find(q)
            elif n >= 2 and names:
                marks = ",".join("?" * len(names))
                rows = self._rows(f"SELECT file, idx FROM units WHERE norm=? AND name IN ({marks}) LIMIT 6", (q, *names))
                rows = rows if len(rows) <= 5 else []
            else:
                continue
            if rows:
                found.append(rows)
        if not found:
            return False
        # 지금 파일 안에서 앞쪽(가까운 다음 대사)에 있으면 그쪽
        if self.file is not None:
            ahead = [(f, i) for rows in found for f, i in rows if f == self.file and i > self.idx - BACK]
            if ahead:
                self.lock(self.file, min(ahead, key=lambda r: r[1])[1], "같은 스토리")
                return True
        cands = set(found[0])
        for rows in found[1:]:  # 한 화면의 여러 줄(대사 + 선택지 등)은 같은 장면에 있어야 함
            near = {(f, i) for f, i in cands for g, j in rows if f == g and abs(i - j) <= NEAR}
            cands = near or cands
        if self.pending:  # 지난 화면에서 못 정한 후보 바로 뒤에 오는 것
            follow = {(f, i) for f, i in cands for g, j in self.pending if f == g and 0 < i - j <= NEAR}
            cands = follow or cands
        if len({f for f, _ in cands}) == 1:
            f = next(iter(cands))[0]
            self.lock(f, max(i for g, i in cands if g == f), "대사")
            return True
        self.pending = sorted(cands)[:MAX_CANDS]
        return False

    def _match_window(self, qs, story_ui=True):
        """지금 위치 앞뒤 대본 글과 화면 줄을 맞댄다. 스토리 단추가 안 보이면(대사 기록 화면 등) 통째로 같은 글만."""
        rows = self._window(self.file, self.idx - BACK, self.idx + AHEAD)
        if not rows:
            return {}
        names = {r[3]: r[4] for r in rows if r[3] and r[4]}
        units = [(r[0], r[5], r) for r in rows]
        # 같은 화자가 이어 말하는 multiline은 한 대사창에 이어 붙어 나온다: 붙인 글도 후보로
        for a, b in zip(rows, rows[1:]):
            if a[2] == b[2] == "multiline" and a[3] == b[3]:
                units.append((b[0], a[5] + b[5], (b[0], b[1], b[2], b[3], b[4], a[5] + b[5], f"{a[6]} {b[6]}")))
        out = {}
        for i, q in qs:
            if q in names:
                if story_ui:
                    out[i] = {"kind": "name", "ko": names[q], "zh": q}
                continue
            best = None
            for idx, u, r in units:
                kind, score = compare(q, u)
                if not kind:
                    continue
                # 아직 몇 글자만 나온 대사는 바로 다음 대사일 때만 믿음
                if kind == "head" and len(q) < 3 and not (self.idx <= idx <= self.idx + 1):
                    continue
                if not story_ui and (kind != "full" or score < 90):
                    continue
                rank = (score, -abs(idx - (self.idx + 1)))
                if best is None or rank > best[0]:
                    best = (rank, kind, r)
            if best:
                _, kind, r = best
                out[i] = {"kind": kind, "unit": (self.file, r[0]), "zh": r[5], "ko": display(r[6]), "name_ko": r[4],
                          "ukind": r[2], "score": best[0][0]}
        moved = [v["unit"][1] for v in out.values() if v.get("unit") and v["score"] >= 84 and v["kind"] != "part"]
        if moved:
            self.idx = max(self.idx, max(moved))
        return out

    def observe(self, texts, story_ui):
        """texts: {줄 번호: OCR 글}. story_ui: 스토리 화면 단추('跳过'·'自动')가 보이는가.

        반환 {줄 번호: {"kind": full/head/part/name, "ko": 번역, "zh": 대본 원문, "unit": (file, idx)}}
        """
        if not self.db:
            return {}
        now = time.time()
        self.ui_now = story_ui
        if story_ui:
            self.ui_at = now
        if self.file is not None and now - max(self.ui_at, self.seen_at) > UNLOCK_AFTER:
            self.unlock()
        qs = [(i, self._q(t)) for i, t in texts.items()]
        qs = [(i, q) for i, q in qs if HAN.search(q)]
        out = self._match_window(qs, story_ui) if self.file is not None else {}
        rest = [(i, q) for i, q in qs if i not in out]
        if rest and (story_ui or self.file is not None) and self._acquire(rest, [q for _, q in qs]):
            out.update(self._match_window(rest, story_ui))
        if any(v.get("unit") for v in out.values()):
            self.seen_at = now
        return out

    def full_box(self, unit, box, line_h, first_text, unit_zh, wrap_w, screen):
        """아직 나오는 중인 대사가 다 나왔을 때 차지할 자리 (대사마다 한 번 정해 두고, 실제 글이 더 크면 넓힘).

        글자 폭은 지금 보이는 첫 줄로 재고, 한 줄 폭(wrap_w, 대사창은 게임 화면 폭의 57% 안팎)을 넘으면 줄 수를 늘린다.
        """
        x0, y0, x1, y1 = box
        units = sum(0.55 if ord(c) < 128 else 1.0 for c in first_text if not c.isspace())
        cw = (x1 - x0) / max(1.0, units) if units >= 2 else line_h * 0.9
        cw = min(max(cw, line_h * 0.6), line_h * 1.2)
        full = sum(0.55 if ord(c) < 128 else 1.0 for c in unit_zh) * cw
        n_lines = max(1, -(-int(full) // max(1, int(wrap_w))))
        pitch = line_h * 1.3
        # 폭은 대사창 한 줄 폭 전부: 한국어는 중국어보다 길어 짧은 대사('没错。' → '그래, 그거야.')도 글자를 줄이지 않고 옆으로 씀
        est = (x0, y0, int(min(x0 + wrap_w, screen[0] - 4)), int(min(y0 + line_h + (n_lines - 1) * pitch, screen[1] - 4)))
        old = self.boxes.get(unit)
        if old and abs(old[0] - x0) <= line_h and abs(old[1] - y0) <= line_h:
            est = old
        est = (min(est[0], x0), min(est[1], y0), max(est[2], x1), max(est[3], y1))
        self.boxes[unit] = est
        return est

    def title_panel(self):
        """새 스토리를 잡은 직후 잠깐 보여 줄 제목 ('HS-4 회서리 농사에 관해 · 작전 전'). 없으면 None."""
        now = time.time()
        if not self.info or now - self.locked_at > TITLE_SHOW or not getattr(self, "ui_now", False):
            return None  # 지금 화면에 스토리 단추가 보일 때만 (정산 화면 등으로 넘어가면 숨김)
        path, code, title, title_ko, tag_ko = self.info
        if not (title_ko or title):
            return None
        return " ".join(x for x in (code, title_ko or title) if x) + (f" · {tag_ko}" if tag_ko else "")
