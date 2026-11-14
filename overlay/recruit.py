"""공개모집 태그 조합 계산 (out/recruit.json, build_recruit.py가 만듦).

모집 시간 9시간 기준으로, 화면의 태그 5개 중 1~3개를 고른 조합마다 나올 수 있는 오퍼레이터를 구해
가장 낮은 등급이 4★ 이상인 조합(=4★ 이상 확정)을 알려 준다.
  - 6★는 '고급 특별 채용'을 골랐을 때만 나오고, 그때는 6★ 확정
  - '특별 채용'을 고르면(고급 특별 채용 없이) 5★ 확정
  - '로봇'은 3:50 이하로 두면 1★ 로봇 확정 (9시간에는 1★가 안 나오므로 따로 알림)
"""

import json
from itertools import combinations
from pathlib import Path

NINE_HOURS = 540
ROBOT_TIME = 230  # 3:50
SENIOR, TOP = 14, 11   # 특별 채용, 고급 특별 채용
ROBOT = 28


class Recruit:
    def __init__(self, path):
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        self.tags = {t["id"]: t for t in d["tags"]}
        self.by_zh = {t["zh"]: t["id"] for t in d["tags"]}
        self.ops = [{**o, "tags": frozenset(o["tags"])} for o in d["ops"]]
        self.times = d["times"]
        self.special = {int(k): v for k, v in d["special"].items()}

    def rarity_range(self, minutes):
        lo, hi = self.times[0][1:]
        for start, a, b in self.times:
            if minutes >= start:
                lo, hi = a, b
        return lo, hi

    def candidates(self, combo, minutes=NINE_HOURS):
        """이 조합으로 나올 수 있는 오퍼레이터 (등급 높은 순)."""
        lo, hi = self.rarity_range(minutes)
        allowed = set(range(lo, hi + 1))
        # 특별 태그는 그 등급만 나오게 한다 (고급 특별 채용 → 6★, 특별 채용 → 5★)
        for tag in (TOP, SENIOR):
            if tag in combo:
                allowed = set(self.special.get(tag, []))
                break
        found = [o for o in self.ops if combo <= o["tags"] and o["rarity"] in allowed]
        return sorted(found, key=lambda o: (-o["rarity"], o["ko"]))

    def combos(self, tag_ids, minutes=NINE_HOURS, min_rarity=4):
        """[(태그 id 튜플, 최저 등급, 오퍼레이터 목록)] — 최저 등급이 min_rarity 이상인 조합만, 좋은 순.

        더 적은 태그로 같은 결과가 나오는 조합은 뺀다 (특별 채용 + 근거리 == 특별 채용 이면 둘째는 생략).
        """
        tag_ids = [t for t in dict.fromkeys(tag_ids) if t in self.tags]
        results = []
        for n in (1, 2, 3):
            for combo in combinations(tag_ids, n):
                ops = self.candidates(frozenset(combo), minutes)
                if not ops:
                    continue
                low = min(o["rarity"] for o in ops)
                if low < min_rarity:
                    continue
                ids = [o["id"] for o in ops]
                if any(set(c) < set(combo) and [o["id"] for o in r] == ids for c, _, r in results):
                    continue
                results.append((combo, low, ops))
        results.sort(key=lambda r: (-r[1], len(r[2]), len(r[0])))
        return results

    def robot(self, tag_ids):
        """'로봇' 태그가 있으면 3:50으로 1★ 로봇이 확정되는지."""
        return ROBOT in tag_ids and all(o["rarity"] == 1 for o in self.candidates(frozenset([ROBOT]), ROBOT_TIME))

    def tag_ids(self, texts):
        """OCR 글자들 중 태그 이름과 정확히 같은 것의 id (나온 순서, 중복 없이)."""
        return list(dict.fromkeys(self.by_zh[t] for t in texts if t in self.by_zh))

    def ko(self, tag):
        return self.tags[tag]["ko"]

    def describe(self, tag_ids, max_rows=6, max_names=5, owned=None):
        """화면 패널에 쓸 줄들: [(글, 등급)]. 확정 조합이 없으면 그렇다고 한 줄.

        owned(보유 오퍼레이터 char_id 모음)가 있으면 아직 없는 오퍼레이터 이름 뒤에 '(신규)'를 붙이고 앞에 둔다.
        """
        rows = []
        if self.robot(tag_ids):
            rows.append(("로봇 → 1★ 확정 (3:50)", 1))
        for combo, low, ops in self.combos(tag_ids)[:max_rows]:
            if owned:
                ops = sorted(ops, key=lambda o: o["id"] in owned)  # 신규 먼저 (정렬은 안정적이라 등급 순서 유지)
            label = lambda o: o["ko"] + (" (신규)" if owned and o["id"] not in owned else "")  # noqa: E731
            names = ", ".join(label(o) for o in ops[:max_names]) + (" …" if len(ops) > max_names else "")
            rows.append((f"{' + '.join(self.ko(t) for t in combo)} → {low}★: {names}", low))
        if not rows:
            rows.append(("4★ 확정 조합 없음", 3))
        return rows
