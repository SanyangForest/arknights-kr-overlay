"""육성 계산기: 오퍼레이터 목표(정예화·스킬 레벨·마스터리)에 필요한 재료를 창고 재고와 비교하는 웹 페이지를 만든다.

데이터를 페이지 안에 넣은 HTML 한 장 (overlay/data/planner.html)을 만들어 브라우저로 연다 (트레이 '육성 계산기 열기').
  - 비용: character_table (phases[].evolveCost, allSkillLvlup[].lvlUpCost, skills[].levelUpCostCond[].levelUpCost),
          정예화 용문폐는 gamedata_const.evolveGoldCost
  - 재고: inventory.json, 보유 오퍼레이터: owned.json (창고·오퍼레이터 목록 화면을 넘기면 오버레이가 기록)
  - 재료마다 가장 효율 좋은 스테이지: 펭귄 드롭 통계(drops.py)에서 개당 이성이 가장 적은 곳
이름은 한국 서버 표기(없으면 사전 번역 → 중국어).
"""

import json
import os
from pathlib import Path

import depot
import drops
import owned
import paths

GAME = paths.GAME
OUT = paths.USER_DATA / "planner.html"
LMD = "4001"


def _load(region, name):
    return json.loads((GAME / region / "gamedata" / "excel" / f"{name}.json").read_text(encoding="utf-8"))


def _costs(lst):
    return [[c["id"], c["count"]] for c in (lst or [])]


def build():
    cn_chars, kr_chars = _load("cn", "character_table"), _load("kr", "character_table")
    cn_skills, kr_skills = _load("cn", "skill_table"), _load("kr", "skill_table")
    gold = _load("cn", "gamedata_const").get("evolveGoldCost", [])
    have_ops = owned.load()
    ops = []
    for cid, c in cn_chars.items():
        if not cid.startswith("char_") or c.get("isNotObtainable") or c.get("profession") in ("TOKEN", "TRAP"):
            continue
        rarity = int(str(c["rarity"]).split("_")[-1]) if isinstance(c["rarity"], str) else c["rarity"] + 1
        elite = []
        for p, phase in enumerate(c["phases"][1:], start=1):
            cost = _costs(phase.get("evolveCost"))
            if gold and rarity - 1 < len(gold) and p - 1 < len(gold[rarity - 1]) and gold[rarity - 1][p - 1]:
                cost.append([LMD, gold[rarity - 1][p - 1]])
            elite.append(cost)
        skills = []
        for s in c.get("skills") or []:
            sid = s.get("skillId")
            if not sid:
                continue
            ko = kr_skills.get(sid, {}).get("levels", [{}])[0].get("name") or cn_skills.get(sid, {}).get("levels", [{}])[0].get("name", sid)
            skills.append({"name": ko, "mastery": [_costs(m.get("levelUpCost")) for m in s.get("levelUpCostCond") or []]})
        ops.append({"id": cid, "ko": kr_chars.get(cid, c)["name"], "zh": c["name"], "rarity": rarity, "owned": cid in have_ops,
                    "elite": elite, "skillLv": [_costs(x.get("lvlUpCost")) for x in c.get("allSkillLvlup") or []],
                    "skills": skills})
    ops.sort(key=lambda o: (not o["owned"], -o["rarity"], o["ko"]))

    d = drops.Drops()
    stage_info = {s: (code, ap) for code, entries in d.by_code.items() for s, _n, ap in entries}
    stages = {}
    for sid, lst in d.matrix.items():
        code_ap = stage_info.get(sid)
        if not code_ap:
            continue
        for iid, rate in lst:
            per = code_ap[1] / rate
            if iid not in stages or per < stages[iid][1]:
                stages[iid] = (code_ap[0], per)
    inv = depot.load()
    used = {i for o in ops for lst in (o["elite"] + o["skillLv"] + [m for s in o["skills"] for m in s["mastery"]]) for i, _n in lst}
    items = {}
    for iid in used:
        name, rarity = d.items.get(iid, (iid, 1))
        best = stages.get(iid)
        items[iid] = {"ko": name, "rarity": rarity, "have": int(inv.get(iid, 0)),
                      "best": [best[0], round(best[1], 1)] if best else None}
    data = {"ops": ops, "items": items, "inventoryDate": _date(depot.INVENTORY), "ownedDate": _date(owned.OWNED)}
    html = (paths.CODE / "planner_template.html").read_text(encoding="utf-8").replace(
        "/*DATA*/null", json.dumps(data, ensure_ascii=False))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    return OUT


def _date(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")).get("updated")
    except (OSError, ValueError):
        return None


def open_planner():
    os.startfile(build())


if __name__ == "__main__":
    print(build())
