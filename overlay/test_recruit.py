"""공개모집 조합 계산 확인 (python -m pytest test_recruit.py 또는 python test_recruit.py)."""

from pathlib import Path

from recruit import Recruit

R = Recruit(Path(__file__).resolve().parent.parent / "out" / "recruit.json")
T = R.by_zh


def lows(tags):
    return {tuple(R.ko(t) for t in combo): low for combo, low, _ in R.combos([T[t] for t in tags])}


def test_senior_is_five_star():
    assert lows(["资深干员"])[("특별 채용",)] == 5


def test_top_senior_is_six_star():
    assert lows(["高级资深干员"])[("고급 특별 채용",)] == 6


def test_control_alone_is_four_star():
    # 3★ 중에 제어형이 없으므로 4★ 이상 확정
    assert lows(["控场"])[("제어형",)] >= 4


def test_common_tags_not_guaranteed():
    got = lows(["输出", "近卫干员", "远程位"])
    assert ("딜러",) not in got and ("가드",) not in got


def test_redundant_combo_dropped():
    # 특별 채용 + 근거리는 '특별 채용'만 고른 것과 다르면 남고, 같으면 빠진다
    got = R.combos([T["资深干员"], T["近战位"]])
    assert all(len(c) == 1 or [o["id"] for o in ops] != [o["id"] for o in R.candidates(frozenset([T["资深干员"]]))]
               for c, _, ops in got)


def test_robot():
    assert R.robot([T["支援机械"], T["治疗"]])
    assert not R.robot([T["治疗"]])


def test_describe_has_rows():
    rows = R.describe([T["控场"], T["输出"], T["近卫干员"], T["远程位"], T["新手"]])
    assert rows and rows[0][1] >= 4


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("통과", name)
    for text, low in R.describe([T["控场"], T["爆发"], T["近战位"], T["支援机械"], T["资深干员"]]):
        print(low, text)
