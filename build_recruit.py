"""공개모집 태그 조합 계산용 데이터 (out/recruit.json).

게임 데이터에서 모집 가능한 오퍼레이터와 태그를 모은다.
  - 모집 가능 목록: cn gacha_table.recruitDetail (★ 줄 아래 ' / '로 나뉜 이름)
  - 오퍼레이터 태그: character_table의 직군·위치·tagList, 등급(2★ 신입, 5★ 특별 채용, 6★ 고급 특별 채용), 성별(handbook)
  - 한국어: 태그는 kr gacha_table.gachaTags(공식), 이름은 kr character_table → 사전(out/dict_zh_ko.tsv) → 중국어 그대로
  - 시간별 등급 범위: recruitRarityTable, 특별 태그: specialTagRarityTable

사용법: python build_recruit.py <게임 데이터 경로> [--out out]
"""

import argparse
import json
import re
import sys
from pathlib import Path

from tsvio import unesc

PROFESSION = {"PIONEER": "先锋干员", "WARRIOR": "近卫干员", "TANK": "重装干员", "SNIPER": "狙击干员",
              "CASTER": "术师干员", "MEDIC": "医疗干员", "SUPPORT": "辅助干员", "SPECIAL": "特种干员"}
POSITION = {"MELEE": "近战位", "RANGED": "远程位"}
RARITY_TAG = {2: "新手", 5: "资深干员", 6: "高级资深干员"}
GENDER = {"男": "男性干员", "女": "女性干员"}


def load(root, region, name):
    return json.loads((Path(root) / region / "gamedata" / "excel" / f"{name}.json").read_text(encoding="utf-8"))


def stars(rarity):
    """'TIER_4' 또는 3(0부터 셈) → 4."""
    return int(rarity.split("_")[1]) if isinstance(rarity, str) else int(rarity) + 1


def recruit_pool(detail):
    """recruitDetail 글에서 (등급, 이름) 목록. 이름 칸은 '<@rc.eml>이름</>' 또는 '이름'."""
    pool, rarity = [], 0
    for line in re.sub(r"<[^>]*>", "", detail).replace("\\n", "\n").splitlines():
        line = line.strip()
        if re.fullmatch(r"★+", line):
            rarity = len(line)
        elif line.startswith("-"):
            rarity = 0
        elif rarity and line:
            pool += [(rarity, n.strip()) for n in line.split(" / ") if n.strip()]
    return pool


def gender_of(handbook, char_id):
    m = re.search(r"【性别】\s*([男女])", json.dumps(handbook.get(char_id, {}), ensure_ascii=False))
    return m.group(1) if m else None


def dict_names(path, names):
    """사전에서 이름 번역 (정확히 같은 원문만)."""
    found = {}
    if not path.exists():
        return found
    with open(path, encoding="utf-8") as f:
        next(f)
        for line in f:
            zh, ko = line.split("\t", 2)[:2]
            zh = unesc(zh)
            if zh in names and zh not in found:
                found[zh] = unesc(ko)
    return found


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("data_root")
    ap.add_argument("--out", default="out")
    args = ap.parse_args()

    gacha_cn, gacha_kr = load(args.data_root, "cn", "gacha_table"), load(args.data_root, "kr", "gacha_table")
    chars_cn, chars_kr = load(args.data_root, "cn", "character_table"), load(args.data_root, "kr", "character_table")
    handbook = load(args.data_root, "cn", "handbook_info_table")["handbookDict"]

    ko_tag = {t["tagId"]: t["tagName"] for t in gacha_kr["gachaTags"]}
    tags = [{"id": t["tagId"], "zh": t["tagName"], "ko": ko_tag.get(t["tagId"], t["tagName"])}
            for t in gacha_cn["gachaTags"]]
    tag_id = {t["zh"]: t["id"] for t in tags}

    by_name = {}
    for cid, c in chars_cn.items():
        if cid.startswith("char_") and c.get("name"):
            by_name.setdefault(c["name"], cid)
    pool = recruit_pool(gacha_cn["recruitDetail"])
    need_ko = [name for _, name in pool if by_name.get(name) not in chars_kr]
    from_dict = dict_names(Path(args.out) / "dict_zh_ko.tsv", set(need_ko))

    ops, missing = [], []
    for rarity, name in pool:
        cid = by_name.get(name)
        if not cid:
            missing.append(name)
            continue
        c = chars_cn[cid]
        names = [PROFESSION.get(c["profession"]), POSITION.get(c["position"]), RARITY_TAG.get(stars(c["rarity"])),
                 GENDER.get(gender_of(handbook, cid))] + list(c.get("tagList") or [])
        ids = sorted({tag_id[n] for n in names if n in tag_id})
        ko = chars_kr[cid]["name"] if cid in chars_kr else from_dict.get(name, name)
        ops.append({"id": cid, "zh": name, "ko": ko, "rarity": stars(c["rarity"]), "tags": ids})

    # 시간(분)별 나올 수 있는 등급 범위 (★ 단위). 같은 범위가 이어지면 처음 시간만 남긴다
    times = []
    for minutes, r in sorted(gacha_cn["recruitRarityTable"].items(), key=lambda kv: int(kv[0])):
        rng = [stars(r["rarityStart"]), stars(r["rarityEnd"])]
        if not times or times[-1][1:] != rng:
            times.append([int(minutes)] + rng)
    special = {int(k): [stars(v) for v in vs] for k, vs in gacha_cn["specialTagRarityTable"].items()}

    out = {"tags": tags, "ops": ops, "times": times, "special": special}
    path = Path(args.out) / "recruit.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    untranslated = [o["zh"] for o in ops if o["ko"] == o["zh"] and re.search(r"[一-鿿]", o["zh"])]
    print(f"태그 {len(tags)}개, 모집 가능 {len(ops)}명 → {path}")
    print("시간별 등급:", times, "특별 태그:", special)
    if missing:
        print("character_table에서 못 찾은 이름:", missing)
    if untranslated:
        print("한국어 이름 없음:", untranslated)


if __name__ == "__main__":
    main()
