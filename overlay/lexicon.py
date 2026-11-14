"""사전 기반 재채점: OCR이 사전에 없는 글을 냈을 때, 비슷한 사전 문장 후보를 글자 읽기 모델의 확률로 다시 채점한다.

OCR은 글자마다 가장 그럴듯한 하나만 고르므로 '卫戍'를 '卫成'으로, '胄'를 '胃'로 읽으면 사전에서 못 찾는다.
그 글자 자리의 확률표를 보면 '戍'도 꽤 높은 경우가 많다. 그래서
  1. 읽은 글과 글자가 비슷한 사전 문장을 후보로 뽑고 (rapidfuzz)
  2. 각 후보가 그 확률표에서 나올 가능성(CTC 우도)을 계산해
  3. 처음 읽은 글보다 크게 나쁘지 않은 후보 중 가장 좋은 것으로 바꾼다.
필기 인식의 'lexicon search'(후보를 사전에서 찾아 신경망 출력으로 재채점)와 같은 방법이다.
"""

import math
from collections import defaultdict

import cv2
import numpy as np
from rapidfuzz import fuzz, process

from matcher import HAN, TAG, normalize

MAX_LEN = 30        # 이보다 긴 줄은 다루지 않음 (긴 문장은 줄바꿈 합치기·앞부분 찾기로 처리)
MARGIN = 2.5        # 후보의 음의 로그우도가 처음 읽은 글보다 이만큼(글자당 아님, 전체) 넘게 나쁘면 버림
CUTOFF = 60         # 후보로 볼 글자 유사도 (0~100)


def ctc_nll(logp, label):
    """CTC 음의 로그우도: 확률표 logp (T×C, 로그 확률)에서 글자 번호 목록 label이 나올 가능성. 0번은 빈칸."""
    T = logp.shape[0]
    ext = [0]
    for c in label:
        ext += [c, 0]
    S = len(ext)
    if S > 2 * T + 1:
        return math.inf
    ext = np.array(ext)
    alpha = np.full(S, -np.inf)
    alpha[0] = logp[0, 0]
    if S > 1:
        alpha[1] = logp[0, ext[1]]
    # 같은 글자가 이어지지 않으면 빈칸을 건너뛸 수 있음
    skip = np.zeros(S, dtype=bool)
    skip[2:] = (ext[2:] != 0) & (ext[2:] != ext[:-2])
    for t in range(1, T):
        prev = alpha
        a1 = np.concatenate([[-np.inf], prev[:-1]])
        a2 = np.where(skip, np.concatenate([[-np.inf, -np.inf], prev[:-2]]), -np.inf)
        alpha = np.logaddexp(np.logaddexp(prev, a1), a2) + logp[t, ext]
    return -np.logaddexp(alpha[-1], alpha[-2] if S > 1 else -np.inf)


class Lexicon:
    def __init__(self, matcher, rec):
        """matcher: 사전, rec: RapidOCR의 text_recognizer (확률표를 다시 구할 때 씀)."""
        self.matcher, self.rec = matcher, rec
        chars = rec.postprocess_op.character  # 0번은 'blank'
        self.char_id = {c: i for i, c in enumerate(chars) if i > 0}
        # 길이별 후보 문장 (정규화한 키 → 화면에 보이는 원문)
        self.by_len = defaultdict(list)
        self.raw = {}
        for key, (zh, _ko) in matcher.table.items():
            if 2 <= len(key) <= MAX_LEN and HAN.search(key):
                self.by_len[len(key)].append(key)
                self.raw[key] = TAG.sub("", zh).replace("\\n", "").replace("\n", "")
        self.cache = {}

    def logprobs(self, crop):
        """글자 그림 하나의 확률표 (T×C, 로그)."""
        rec = self.rec
        h, w = crop.shape[:2]
        norm = rec.resize_norm_img(crop, w / max(1, h))[np.newaxis].astype(np.float32)
        preds = rec.session(norm)[0][0]
        return np.log(np.clip(preds, 1e-9, 1.0))

    def candidates(self, text, limit=8):
        q = normalize(text)
        n = len(q)
        pool = [k for L in range(max(2, n - 2), min(MAX_LEN, n + 2) + 1) for k in self.by_len.get(L, ())]
        found = process.extract(q, pool, scorer=fuzz.ratio, limit=limit, score_cutoff=CUTOFF)
        return [k for k, _s, _i in found]

    def ids(self, s):
        out = []
        for c in s:
            if c.isspace():
                continue
            i = self.char_id.get(c)
            if i is None:
                return None
            out.append(i)
        return out

    def correct(self, text, crop):
        """더 그럴듯한 사전 원문으로 바꾼 글 (없으면 None)."""
        if not HAN.search(text) or len(normalize(text)) > MAX_LEN:
            return None
        key = (text, crop.shape, crop.tobytes()[:64])
        if key in self.cache:
            return self.cache[key]
        best = None
        cands = self.candidates(text)
        if cands:
            logp = self.logprobs(crop)
            base_ids = self.ids(text)
            base = ctc_nll(logp, base_ids) if base_ids else math.inf
            scored = []
            for k in cands:
                ids = self.ids(self.raw[k])
                if ids:
                    scored.append((ctc_nll(logp, ids), k))
            if scored:
                nll, k = min(scored)
                if nll - base <= MARGIN:
                    best = self.raw[k]
        self.cache[key] = best
        return best
