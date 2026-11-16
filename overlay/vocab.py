"""화면별 낱말장: 그 화면에서 사전과 정확히 맞았던 문구들. 잘리거나 가려져 일부만 읽힌 글('造站', '可收')을 그 화면의 문구로 알아본다.

사전 전체(52만 줄)에서 두 글자 조각을 찾으면 후보가 수천 개라 쓸 수 없지만, 기반시설 화면에 나오는 문구는 수십 개라
'造站'이 든 것은 '制造站' 하나뿐이다. 화면 종류는 screens.classify가 정한다.

  out/screen_vocab.json         녹화 화면으로 만든 기본 낱말장 (python vocab.py build)
  overlay/data/screen_vocab.json 오버레이를 쓰면서 새로 맞은 문구 (저절로 늘어남)
"""

import json
import sys
import time
from pathlib import Path

from rapidfuzz import fuzz

import paths
from matcher import HAN, normalize

SEED = paths.OUT / "screen_vocab.json"
LEARNED = paths.USER_DATA / "screen_vocab.json"
LEARN_KINDS = {"exact", "alias", "title", "label"}
MAX_EXTRA = 6        # 조각보다 이만큼 넘게 긴 문구는 후보에서 뺀다 (긴 문장 속 두 글자는 우연)
SAVE_EVERY = 60.0


class ScreenVocab:
    def __init__(self, nickname=None):
        self.words = {}
        for path in (SEED, LEARNED):
            try:
                for screen, keys in json.loads(path.read_text(encoding="utf-8")).items():
                    self.words.setdefault(screen, set()).update(keys)
            except (OSError, ValueError):
                pass
        self.nickname = (nickname or "").lower()
        self.learned = {}
        try:
            self.learned = {s: set(k) for s, k in json.loads(LEARNED.read_text(encoding="utf-8")).items()}
        except (OSError, ValueError):
            pass
        self.dirty_at = 0.0

    def learn(self, screen, zh):
        """그 화면에서 사전과 정확히 맞은 문구를 기억한다."""
        key = normalize(zh)
        if screen in (None, "unknown") or len(HAN.findall(key)) < 2 or len(key) > 24:
            return
        if self.nickname and self.nickname[:5] in key.lower():
            return
        if key not in self.words.setdefault(screen, set()):
            self.words[screen].add(key)
            self.learned.setdefault(screen, set()).add(key)
            self.dirty_at = self.dirty_at or time.time()
        if self.dirty_at and time.time() - self.dirty_at > SAVE_EVERY:
            self.save()

    def save(self):
        if not self.dirty_at:
            return
        LEARNED.parent.mkdir(parents=True, exist_ok=True)
        LEARNED.write_text(json.dumps({s: sorted(k) for s, k in self.learned.items()}, ensure_ascii=False, indent=0),
                           encoding="utf-8")
        self.dirty_at = 0.0

    def guess(self, screen, text):
        """일부만 읽힌 글 → 그 화면 낱말장에서 하나로 정해지는 문구(정규화한 사전 키). 없거나 여럿이면 None."""
        words = self.words.get(screen)
        q = normalize(text)
        if not words or len(HAN.findall(q)) < 2:
            return None
        cands = [k for k in words if q in k and len(k) - len(q) <= MAX_EXTRA]
        if not cands and len(q) >= 4:
            cands = [k for k in words if abs(len(k) - len(q)) <= 1 and fuzz.ratio(q, k) >= 80]
        if len(cands) > 1:  # '制造站'과 '制造站已自动补货'처럼 짧은 쪽이 나머지에 다 들어 있으면 짧은 쪽
            short = min(cands, key=len)
            cands = [short] if all(short in k for k in cands) else cands
        return cands[0] if len(cands) == 1 else None


def build(folders):
    """녹화 폴더의 OCR 캐시(frame_*.ocr.json, batch_eval.py가 만듦)로 기본 낱말장을 만든다."""
    from matcher import Matcher
    from screens import classify, load_screens
    m = Matcher(str(paths.OUT / "dict_zh_ko.json"))
    screens = load_screens()
    vocab = ScreenVocab.__new__(ScreenVocab)
    vocab.words, vocab.learned, vocab.dirty_at = {}, {}, 0.0
    vocab.nickname = (m.nickname or "").lower()
    n = 0
    for folder in folders:
        for f in sorted(Path(folder).glob("frame_*.ocr.json")):
            result = json.loads(f.read_text(encoding="utf-8"))
            screen = classify([t for _, t, _ in result], screens)
            if screen == "unknown":
                continue
            n += 1
            for _box, text, _s in result:
                hit = m.lookup(text, screen)
                if hit and hit[0] in LEARN_KINDS:
                    vocab.learn(screen, hit[1])
    SEED.write_text(json.dumps({s: sorted(k) for s, k in sorted(vocab.words.items())}, ensure_ascii=False, indent=0),
                    encoding="utf-8")
    print(f"화면 {n}장 → 낱말장 {len(vocab.words)}종 화면, 문구 {sum(len(k) for k in vocab.words.values())}개 → {SEED}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if sys.argv[1:2] == ["build"]:
        build(sys.argv[2:] or sorted(str(d) for d in paths.CAPTURES.glob("session*") if d.is_dir()))
