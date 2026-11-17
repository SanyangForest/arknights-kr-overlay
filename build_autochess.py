"""위수협의(卫戍协议, act3autochess) 추천 조합 데이터: 게임 데이터의 맹약(盟约)·오퍼레이터 비용으로 만든다 → out/autochess.json

  - 맹약마다: 한국어 이름, 종류(국가 = 3·6명 / 성향 = 2명…), 발동 인원, 소속 오퍼레이터(한국어 이름·비용)
  - 국가 맹약마다 추천 조합:
      초반 3명 = 비용이 가장 낮은 3명 (3명 발동을 빨리)
      완성 6명 = 그 국가 10명 중 6명을 골라, 함께 켜지는 성향 맹약이 가장 많고 그다음 비용 합이 높은(강한) 조합
  - 커뮤니티 추천 조합 (extra/autochess_community.json): bilibili 공략 영상의 조합을 유형별로 정리한 ak-sweep
    (github.com/dazauzai/ak-sweep, MIT)에서 유형마다 즐겨찾기가 가장 많은 것. 제목·요약은 직접 번역해 그 파일에 들어 있음
폰 앱이 meta 'autochess'로 받아 왼쪽 가운데 단추로 여는 패널에 보여 준다 (mobile/export_dict.py).
이름은 사전(out/dict_zh_ko.json)의 번역을 쓴다: 한국 서버에 아직 없는 모드라 직접 번역한 이름이 섞여 있음.

  python build_autochess.py [게임 데이터 경로]
"""

import itertools
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TAG = re.compile(r"<[@$/][^<>]{0,40}>|</>")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    game = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "gamedata"
    act = json.loads((game / "cn/gamedata/excel/activity_table.json").read_text(encoding="utf-8"))
    chars = json.loads((game / "cn/gamedata/excel/character_table.json").read_text(encoding="utf-8"))
    ko = json.loads((ROOT / "out" / "dict_zh_ko.json").read_text(encoding="utf-8"))
    modes = act["activity"]["AUTOCHESS"]
    act_id = sorted(modes)[-1]
    data = modes[act_id][sorted(modes[act_id])[-1]]
    shop = data["charShopChessDatas"]
    # 체스 id(금색 포함) → (오퍼레이터 id, 비용)
    piece = {}
    for cid, s in shop.items():
        piece[cid] = (s["charId"], s["chessLevel"])
        if s.get("goldenChessId"):
            piece[s["goldenChessId"]] = (s["charId"], s["chessLevel"])

    def name_ko(char_id):
        zh = chars.get(char_id, {}).get("name") or char_id
        return ko.get(zh, zh)

    # 오퍼레이터 원문 이름 → 비용 (커뮤니티 조합은 이름으로 적혀 있음)
    cost_of = {}
    for char_id, cost in piece.values():
        zh = chars.get(char_id, {}).get("name")
        if zh:
            cost_of[zh] = min(cost, cost_of.get(zh, cost))

    bonds = []
    for bid, b in data["bondInfoDict"].items():
        members = {}
        for c in b["chessIdList"]:
            if c in piece:
                char_id, cost = piece[c]
                members[char_id] = min(cost, members.get(char_id, cost))
        if not members:
            continue
        need = [int(x) for x in b["activeParamList"] if x.isdigit()]
        # 설명의 '<在场 N名 不同…>' 은 다음 단계 인원 (국가 맹약 6명 등)
        need += [int(n) for n in re.findall(r"在场<@[^>]*>(\d+)</>", b["desc"]) if int(n) not in need]
        is_nation = b.get("bondType") == "SEASON" or len(members) == 10 and need[:1] == [3]
        desc = ko.get(b["desc"])
        bonds.append({
            "id": bid, "zh": b["name"], "ko": ko.get(b["name"], b["name"]),
            "kind": "nation" if is_nation else "style", "need": sorted(need),
            "desc": TAG.sub("", desc).replace("\\n", "\n").strip() if desc else "",
            "members": sorted(({"char": c, "ko": name_ko(c), "cost": cost} for c, cost in members.items()),
                              key=lambda m: (m["cost"], m["ko"])),
        })
    # 한 명부터 켜지는 특수 맹약(독행: 1명일 때만, 조화)은 조합 점수에서 뺀다
    style = [b for b in bonds if b["kind"] == "style" and (b["need"] or [2])[0] >= 2]
    style_of = {}
    for b in style:
        for m in b["members"]:
            style_of.setdefault(m["char"], []).append(b)

    comps = []
    for b in (x for x in bonds if x["kind"] == "nation"):
        mem = b["members"]
        best = None
        for six in itertools.combinations(mem, min(6, len(mem))):
            counts = {}
            for m in six:
                for s in style_of.get(m["char"], []):
                    counts[s["id"]] = counts.get(s["id"], 0) + 1
            on = [(s, counts[s["id"]]) for s in style if counts.get(s["id"], 0) >= (s["need"] or [2])[0]]
            score = (len(on), sum(n for _, n in on), sum(m["cost"] for m in six))
            if best is None or score > best[0]:
                best = (score, six, on)
        _, six, on = best
        comps.append({
            "bond": b["ko"], "zh": b["zh"], "need": b["need"],
            "early": [{"ko": m["ko"], "cost": m["cost"]} for m in mem[:3]],
            "full": [{"ko": m["ko"], "cost": m["cost"]} for m in sorted(six, key=lambda m: m["cost"])],
            "extra": [{"ko": s["ko"], "count": n} for s, n in sorted(on, key=lambda x: -x[1])],
        })
    bond_ko = {b["zh"]: b["ko"] for b in bonds}

    def op(zh):
        name = ko.get(zh, zh).strip("'\"‘’“”")
        return {"ko": name, "cost": cost_of.get(zh) or cost_of.get(zh.strip("'\"‘’“”"), 0)}

    community, stats = [], ""
    cpath = ROOT / "extra" / "autochess_community.json"
    if cpath.exists():
        c = json.loads(cpath.read_text(encoding="utf-8"))
        stats = c.get("stats_as_of", "")
        for x in c["builds"]:
            community.append({
                "title": x["title"], "summary": x["summary"],
                "bonds": [bond_ko.get(z, ko.get(z, z)) for z in x["bonds"]],
                "carry": [op(z)["ko"] for z in x["carry"]],
                "stages": [{"label": st["label"], "ops": [op(z) for z in st["ops"]]} for st in x["stages"]],
                "author": x["author"], "fav": x["fav"], "published": x["published"][:10],
            })
    missing = sorted({o["ko"] for x in community for st in x["stages"] for o in st["ops"] if not o["cost"]})
    out = {"activity": act_id, "version": sorted(modes[act_id])[-1], "bonds": bonds, "comps": comps,
           "community": community, "community_stats": stats,
           # 위수협의 화면 알아보기: 로비 고정 문구, 또는 맹약 이름이 한 화면에 셋 이상
           "anchors": ["卫戍协议", "联合模拟", "协议数据库", "卫戍百科", "协议规则", "物资调配处"],
           "bond_names": sorted({b["zh"] for b in bonds})}
    (ROOT / "out" / "autochess.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"위수협의 {act_id}: 맹약 {len(bonds)}개 (국가 {len(comps)}), 추천 조합 {len(comps)}개, "
          f"커뮤니티 조합 {len(community)}개 → out/autochess.json")
    if missing:
        print("  비용을 못 찾은 이름:", ", ".join(missing))
    for c in comps[:3]:
        print(" ", c["bond"], "완성:", ", ".join(f"{m['ko']}({m['cost']})" for m in c["full"]),
              "+", ", ".join(f"{e['ko']} {e['count']}" for e in c["extra"]))


if __name__ == "__main__":
    main()
