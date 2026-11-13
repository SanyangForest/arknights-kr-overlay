"""중국 서버(zh_CN) → 한국 서버(ko_KR) 텍스트 사전 생성기.

공개 게임 데이터(ArknightsAssets/ArknightsGamedata의 cn/, kr/)를 비교해서
같은 위치에 있는 문자열끼리 짝지어 zh→ko 사전을 만든다.

  1. i18n/string_map.txt : [KEY]텍스트 형식, 키로 매칭
  2. gamedata/excel/*.json : 같은 JSON 경로의 문자열끼리 매칭
  3. gamedata/story/**.txt : 스크립트 명령어를 기준점(anchor)으로 대사 줄 정렬 후 매칭

출력 (out/):
  dict_zh_ko.json      최종 사전 {zh: ko}
  dict_zh_ko.tsv       검토용 (zh, ko, 출현수, 출처)
  conflicts.tsv        같은 zh에 서로 다른 ko가 붙은 경우
  untranslated.tsv     한국 서버에 대응 문장이 없는 중국어 (번역 대상)
  report.md            통계

사용법:
  python build_dict.py <ArknightsGamedata 경로> [--out out]
"""

import argparse
import csv
import difflib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from tsvio import esc, unesc

HAN = re.compile(r"[\u4e00-\u9fff]")
PLACEHOLDER = re.compile(r"\{[^{}]*\}")
QUOTED = r'"(?:[^"\\]|\\.)*"|' + r"'(?:[^'\\]|\\.)*'"
ATTR = re.compile(rf"(\w+)\s*=\s*({QUOTED})")
CMD = re.compile(r"^\[\s*([A-Za-z]+)\s*(?:\((.*)\))?\s*\]\s*(.*)$", re.S)
NAME_LINE = re.compile(rf"^\[(\s*name\s*=\s*(?:{QUOTED})(?:\s*,\s*\w+\s*=\s*(?:{QUOTED}|[^,\]\s]+))*)\s*\]\s*(.*)$", re.S)
# 스토리 폴더에 있지만 게임 대사가 아닌 파일 (집계용 리포트 등)
NON_SCRIPT_FILES = {"avg_segment_report.txt"}
# 뒤에 붙은 텍스트가 화면에 나오지 않는 명령어 (HEADER 뒤는 개발용 메모)
IGNORE_TAIL = {"header"}
# 스토리 명령어별로 화면에 표시되는 텍스트 속성 (options는 ';'로 나눠서 따로 처리)
TEXT_ATTRS = {
    "subtitle": {"text"},
    "sticker": {"text"},
    "multiline": {"name"},
    "decision": {"option1", "option2", "option3", "option4"},
    "interlude": {"char"},
}

# 용어집에 넣을 excel 필드 ('...name'으로 끝나는 필드는 자동 포함)
TERM_FIELDS = {"appellation", "title"}
TERM_MAX_LEN = 16

SOURCE_PRIORITY = {"i18n": 0, "excel": 1, "story": 2, "template": 3, "bracket": 4, "community": 5, "claude": 6}


def needs_translation(s):
    return isinstance(s, str) and HAN.search(s) is not None


def placeholders_match(zh, ko):
    return sorted(PLACEHOLDER.findall(zh)) == sorted(PLACEHOLDER.findall(ko))


class Collector:
    def __init__(self):
        # zh -> Counter[ko], zh -> Counter[source]
        self.pairs = defaultdict(Counter)
        self.sources = defaultdict(Counter)
        # zh -> set of locations (번역 대상 목록용)
        self.missing = defaultdict(set)
        # 고유명사 후보: zh -> Counter["파일:필드"] (용어집용)
        self.terms = defaultdict(Counter)
        self.stats = Counter()

    def add(self, zh, ko, source, where):
        if not needs_translation(zh):
            return
        if not isinstance(ko, str) or not ko.strip() or ko == zh or HAN.search(ko):
            self.stats[f"{source}:rejected_empty_or_han"] += 1
            self.miss(zh, where)
            return
        # 한국 서버 미사용 자리표시자. 영문 자체는 정상 번역일 수 있음 (예: 灰烬 → Ash)
        if ko.startswith("UNUSED"):
            self.stats[f"{source}:rejected_unlocalized"] += 1
            self.miss(zh, where)
            return
        if not placeholders_match(zh, ko):
            self.stats[f"{source}:rejected_placeholder"] += 1
            self.miss(zh, where)
            return
        self.pairs[zh][ko] += 1
        self.sources[zh][source] += 1
        self.stats[f"{source}:pairs"] += 1

    def miss(self, zh, where):
        if needs_translation(zh):
            self.missing[zh].add(where)


# ---------------------------------------------------------------- i18n

def read_string_map(path):
    out = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        m = re.match(r"^\[([^\]]+)\](.*)$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def collect_i18n(root, col):
    cn = read_string_map(root / "cn/i18n/string_map.txt")
    kr = read_string_map(root / "kr/i18n/string_map.txt")
    for key, zh in cn.items():
        if key in kr:
            col.add(zh, kr[key], "i18n", "i18n")
        else:
            col.miss(zh, "i18n")


# ---------------------------------------------------------------- excel

def is_term_field(field):
    f = field.lower()
    return f.endswith("name") or f in TERM_FIELDS


def list_id_key(items):
    """항목을 식별하는 키 (id, tagId, bgId 등). 모든 항목에 있고 값이 겹치지 않아야 한다."""
    if not items or not all(isinstance(x, dict) for x in items):
        return None
    keys = [k for k in items[0] if k == "id" or (k.endswith("Id") and "sort" not in k.lower())]
    for k in keys:
        vals = [x.get(k) for x in items]
        if all(isinstance(v, (str, int)) for v in vals) and len(set(vals)) == len(vals):
            return k
    return None


def pair_list(zh, ko):
    """중섭 목록의 각 항목에 맞는 한섭 항목. 한섭에 빠진 항목이 있으면 순서가 밀리므로 id로 짝짓는다."""
    if not isinstance(ko, list):
        return [None] * len(zh)
    key = list_id_key(zh)
    if key and all(isinstance(x, dict) for x in ko):
        by_id = {x.get(key): x for x in ko}
        return [by_id.get(x[key]) for x in zh]
    return [ko[i] if i < len(ko) else None for i in range(len(zh))]


def walk_pair(zh, ko, col, where, field=""):
    if isinstance(zh, dict):
        for k, v in zh.items():
            walk_pair(v, ko.get(k) if isinstance(ko, dict) else None, col, where, k)
    elif isinstance(zh, list):
        # id 없이 순서로만 짝짓는 목록은 한섭이 중간 항목을 빼면 뒤가 통째로 밀린다 (tip_table, 2026-10-28 한섭)
        if (isinstance(ko, list) and len(ko) != len(zh) and min(len(zh), len(ko)) > 1 and not list_id_key(zh)
                and HAN.search(json.dumps(zh[:3], ensure_ascii=False))):
            col.stats["excel:unaligned_list"] += 1
            print(f"[주의] 순서로 짝짓는 목록의 길이가 다름: {where} {field} (중섭 {len(zh)} / 한섭 {len(ko)}) "
                  "→ 짝이 밀렸으면 extra/override.tsv로 바로잡기", flush=True)
        for v, kv in zip(zh, pair_list(zh, ko)):
            walk_pair(v, kv, col, where, field)
    elif isinstance(zh, str):
        if is_term_field(field) and needs_translation(zh) and len(zh) <= TERM_MAX_LEN:
            col.terms[zh][f"{where.removeprefix('excel/').removesuffix('.json')}:{field}"] += 1
        if ko is None:
            col.miss(zh, where)
        else:
            col.add(zh, ko, "excel", where)


def collect_excel(root, col):
    cn_dir = root / "cn/gamedata/excel"
    kr_dir = root / "kr/gamedata/excel"
    for cn_file in sorted(cn_dir.glob("*.json")):
        kr_file = kr_dir / cn_file.name
        zh = json.loads(cn_file.read_text(encoding="utf-8"))
        ko = json.loads(kr_file.read_text(encoding="utf-8")) if kr_file.exists() else None
        walk_pair(zh, ko, col, f"excel/{cn_file.name}")
        col.stats["excel:files"] += 1
    # 전투 중 '새 적' 소개창의 짧은 설명('喝了点酒的杜林人，初始拥有…')은 excel이 아니라 적 데이터베이스에 있다
    enemy = "gamedata/levels/enemydata/enemy_database.json"
    if (root / "cn" / enemy).exists():
        zh = json.loads((root / "cn" / enemy).read_text(encoding="utf-8"))
        ko = json.loads((root / "kr" / enemy).read_text(encoding="utf-8")) if (root / "kr" / enemy).exists() else None
        walk_pair(zh, ko, col, "levels/enemy_database.json")


# ---------------------------------------------------------------- story

def parse_attrs(s):
    return [(k.lower(), v[1:-1]) for k, v in ATTR.findall(s)]


def story_tokens(text):
    """스토리 스크립트를 (anchor 토큰, [(종류, 텍스트)]) 목록으로 변환.

    언어와 무관한 명령어 줄은 그대로 anchor가 되고, 텍스트가 들어있는 줄은
    'TEXT:종류' 토큰이 된다. 두 언어의 토큰열을 정렬하면 명령어가 기준점 역할을 한다.
    """
    tokens = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        m = NAME_LINE.match(line)
        if m:
            # [name="X", avatarId="...", isAvatarRight="..."]대사
            attrs = dict(parse_attrs(m.group(1)))
            tokens.append(("TEXT:dialog", [("name", attrs["name"]), ("text", m.group(2).strip())]))
            continue
        m = CMD.match(line)
        if not m:
            tokens.append(("TEXT:narration", [("text", line)]))
            continue
        cmd, args, tail = m.group(1).lower(), m.group(2) or "", m.group(3).strip()
        texts = []
        for k, v in parse_attrs(args):
            if k == "options":
                texts += [("text", o) for o in v.split(";")]
            elif k in TEXT_ATTRS.get(cmd, ()):
                texts.append((k, v))
        if tail and cmd not in IGNORE_TAIL:
            texts.append(("text", tail))
        if texts:
            tokens.append((f"TEXT:{cmd}:" + ",".join(k for k, _ in texts), texts))
        else:
            # 텍스트가 없는 명령어: 공백 제거해서 anchor로 사용
            tokens.append((re.sub(r"\s+", "", line).lower(), []))
    return tokens


def collect_story(root, col):
    cn_dir = root / "cn/gamedata/story"
    kr_dir = root / "kr/gamedata/story"
    for cn_file in sorted(cn_dir.rglob("*.txt")):
        if cn_file.name in NON_SCRIPT_FILES:
            continue
        rel = cn_file.relative_to(cn_dir).as_posix()
        where = f"story/{rel}"
        kr_file = kr_dir / rel
        zh_text = cn_file.read_text(encoding="utf-8-sig")

        if rel.startswith("[uc]info/"):
            # 스토리 요약: 파일 전체가 문장 하나
            if kr_file.exists():
                col.add(zh_text.strip(), kr_file.read_text(encoding="utf-8-sig").strip(), "story", where)
            else:
                col.miss(zh_text.strip(), where)
            continue

        zh_tok = story_tokens(zh_text)
        if not kr_file.exists():
            col.stats["story:cn_only_files"] += 1
            for _, texts in zh_tok:
                for kind, t in texts:
                    col.miss(t, where)
                    if kind == "name" and needs_translation(t) and len(t) <= TERM_MAX_LEN:
                        col.terms[t]["story:speaker"] += 1
            continue

        col.stats["story:files"] += 1
        ko_tok = story_tokens(kr_file.read_text(encoding="utf-8-sig"))
        sm = difflib.SequenceMatcher(None, [t[0] for t in zh_tok], [t[0] for t in ko_tok], autojunk=False)
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == "equal":
                for (_, zt), (_, kt) in zip(zh_tok[i1:i2], ko_tok[j1:j2]):
                    for (kind, z), (_, k) in zip(zt, kt):
                        col.add(z, k, "story", where)
                        if kind == "name" and needs_translation(z) and len(z) <= TERM_MAX_LEN:
                            col.terms[z]["story:speaker"] += 1
            else:
                for _, texts in zh_tok[i1:i2]:
                    for _, t in texts:
                        if needs_translation(t):
                            col.stats["story:unaligned_segments"] += 1
                        col.miss(t, where)


# ---------------------------------------------------------------- 숫자 패턴

NUMBER = re.compile(r"\d+(?:\.\d+)?")
TEMPLATE_MIN_SHARE = 0.6


def number_template(zh, ko):
    """'向好友赠送4份线索' / '친구에게 단서 4개 선물' → ('向好友赠送<0>份线索', '친구에게 단서 <0>개 선물').

    zh와 ko의 숫자 구성이 같고 값이 모두 달라 위치를 확정할 수 있을 때만 만든다.
    """
    zn = NUMBER.findall(zh)
    if not zn or len(set(zn)) != len(zn) or sorted(zn) != sorted(NUMBER.findall(ko)):
        return None
    index = {v: i for i, v in enumerate(zn)}
    it = iter(range(len(zn)))
    zh_t = NUMBER.sub(lambda m: f"<{next(it)}>", zh)
    ko_t = NUMBER.sub(lambda m: f"<{index[m.group()]}>", ko)
    return zh_t, ko_t


def build_templates(final, col):
    votes = defaultdict(Counter)
    for zh, ko in final.items():
        t = number_template(zh, ko)
        if t:
            votes[t[0]][t[1]] += sum(col.pairs[zh].values())
    templates = {}
    for zh_t, kos in votes.items():
        ko_t, n = kos.most_common(1)[0]
        if n / sum(kos.values()) >= TEMPLATE_MIN_SHARE:
            templates[zh_t] = ko_t
    return templates


def fill_templates(untranslated, templates):
    filled = {}
    for zh in untranslated:
        nums = NUMBER.findall(zh)
        if not nums:
            continue
        it = iter(range(len(nums)))
        ko_t = templates.get(NUMBER.sub(lambda m: f"<{next(it)}>", zh))
        if ko_t is not None:
            filled[zh] = re.sub(r"<(\d+)>", lambda m: nums[int(m.group(1))], ko_t)
    return filled


# ---------------------------------------------------------------- output

BRACKET_ZH = re.compile(r"<([^<>@$/][^<>]{0,19})>|【([^【】]{1,20})】|「([^「」]{1,20})」|《([^《》]{1,20})》")
BRACKET_KO = re.compile(r"<([^<>@$/][^<>]{0,29})>|\[([^\[\]]{1,30})\]|「([^「」]{1,30})」|《([^《》]{1,30})》")


COLOR_SPAN = re.compile(r"<color=\"?(#?[0-9A-Fa-f]{6,8})\"?>([^<>]{1,30})</color>")
RICH_SPAN = re.compile(r"<([@$][\w.]+)>([^<>]{1,30})</>")


def _spans(pattern, s, key_group, text_group):
    return [(m.group(key_group), m.group(text_group).strip()) for m in pattern.finditer(s)]


def bracket_terms(final, col):
    """공식 번역 짝에서 문장 안에 표시된 이름끼리 짝짓는다 (화면에는 그 이름만 제목으로 나오는 경우가 있음).

    - 색깔 표시 <color=#..>이름</color>, 강조 태그 <@ba.kw>이름</>: 같은 색·태그끼리 순서대로
    - 괄호 <이름>, 【이름】, 「이름」, 《이름》: 괄호 종류와 개수가 같을 때 순서대로
    표시가 하나뿐인 문장은 2표, 여러 개인 문장은 순서가 바뀌었을 수 있어 1표. 2표 이상이고 다른 번역보다 두 배 많을 때만 쓴다.
    """
    votes = defaultdict(Counter)

    def vote(zs, ks):
        if not zs or len(zs) != len(ks) or [a for a, _ in zs] != [a for a, _ in ks]:
            return
        weight = 2 if len(zs) == 1 else 1
        for (_, z), (_, k) in zip(zs, ks):
            # 숫자가 같고, 한국어 쪽이 너무 짧지 않을 때만 (태그가 일부만 감싼 '阻拦落石 → 낙석' 같은 것 제외)
            if (HAN.search(z) and not HAN.search(k) and 2 <= len(z) <= 20 and k
                    and re.findall(r"\d+", z) == re.findall(r"\d+", k)
                    and len(k.replace(" ", "")) >= 0.7 * len(z)):
                votes[z][k] += weight

    for zh, ko in final.items():
        src = col.sources.get(zh, {})
        if not any(src.get(s) for s in ("i18n", "excel", "story")):
            continue
        vote(_spans(COLOR_SPAN, zh, 1, 2), _spans(COLOR_SPAN, ko, 1, 2))
        vote(_spans(RICH_SPAN, zh, 1, 2), _spans(RICH_SPAN, ko, 1, 2))
        plain_zh, plain_ko = TAG_RE.sub("", zh), TAG_RE.sub("", ko)
        # 【】는 한국어에서 []로 쓰므로 종류 번호(1)가 같고, <>·「」·《》도 각각 같은 번호
        zs = [(i, g) for m in BRACKET_ZH.finditer(plain_zh) for i, g in enumerate(m.groups()) if g]
        ks = [(i, g.strip()) for m in BRACKET_KO.finditer(plain_ko) for i, g in enumerate(m.groups()) if g]
        vote(zs, ks)
    out = {}
    for z, kos in votes.items():
        (best, n), *rest = kos.most_common()
        if n >= 2 and (not rest or n > rest[0][1] * 2):
            out[z] = best
    return out


TAG_RE = re.compile(r"<[@$][^<>]{0,40}>|</>|</?color[^<>]{0,20}>|</?[ib]>")


def write_outputs(col, out, root):
    out.mkdir(parents=True, exist_ok=True)
    final = {}
    conflicts = []
    for zh, kos in col.pairs.items():
        (best, n), *rest = kos.most_common()
        final[zh] = best
        if rest:
            conflicts.append((zh, kos))

    templates = build_templates(final, col)
    templated = fill_templates([zh for zh in col.missing if zh not in final], templates)
    for zh, ko in templated.items():
        final[zh] = ko
        col.sources[zh]["template"] += 1

    # 공식 데이터의 잘못된 짝 바로잡기 (extra/override.tsv): 한섭이 문장을 다르게 쪼개 생긴 조각 등. 번역이 비면 지움
    override = Path(__file__).with_name("extra") / "override.tsv"
    if override.exists():
        with open(override, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
                zh, ko = unesc(row["zh"]), unesc(row["ko"])
                if ko:
                    final[zh] = ko
                    col.sources[zh] = Counter({"community": 1})
                else:
                    final.pop(zh, None)

    # 공식 문장 속 괄호 이름: 화면에는 제목으로 따로 나오지만 데이터에는 '<排异反应>…' 처럼 문장 안에만 있는 것
    for zh, ko in bracket_terms(final, col).items():
        if zh not in final:
            final[zh] = ko
            col.sources[zh]["bracket"] += 1

    # 추가 번역 (extra/*.tsv): 공식 번역이 없는 항목에만 넣는다.
    # 커뮤니티 표기(community_*)를 직접 번역(claude_*)보다 먼저 넣어서 우선한다.
    extra = sorted((t for t in Path(__file__).with_name("extra").glob("*.tsv") if t.name != "override.tsv"),
                   key=lambda p: (not p.name.startswith("community"), p.name))
    for tsv in extra:
        kind = "claude" if tsv.name.startswith("claude") else "community"
        with open(tsv, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
                zh = unesc(row["zh"])
                if zh not in final:
                    final[zh] = unesc(row["ko"])
                    col.sources[zh][kind] += 1

    dict_path = out / "dict_zh_ko.json"
    dict_path.write_text(json.dumps(dict(sorted(final.items())), ensure_ascii=False, indent=1), encoding="utf-8")

    with open(out / "dict_zh_ko.tsv", "w", encoding="utf-8", newline="") as f:
        f.write("zh\tko\tcount\tsources\n")
        for zh in sorted(final):
            srcs = ",".join(s for s, _ in sorted(col.sources[zh].items(), key=lambda x: SOURCE_PRIORITY[x[0]]))
            f.write(f"{esc(zh)}\t{esc(final[zh])}\t{sum(col.pairs[zh].values())}\t{srcs}\n")

    with open(out / "conflicts.tsv", "w", encoding="utf-8", newline="") as f:
        f.write("zh\tchosen\talternatives\n")
        for zh, kos in sorted(conflicts, key=lambda x: -sum(x[1].values())):
            alts = " | ".join(f"{esc(k)} ({n})" for k, n in kos.most_common()[1:])
            f.write(f"{esc(zh)}\t{esc(kos.most_common(1)[0][0])} ({kos.most_common(1)[0][1]})\t{alts}\n")

    def term_kinds(zh):
        return ",".join(k for k, _ in col.terms[zh].most_common(3))

    known_terms = sorted((zh for zh in col.terms if zh in final), key=lambda z: -sum(col.terms[z].values()))
    new_terms = sorted((zh for zh in col.terms if zh not in final), key=lambda z: -sum(col.terms[z].values()))
    with open(out / "glossary.tsv", "w", encoding="utf-8", newline="") as f:
        f.write("zh\tko\tseen\tkinds\n")
        for zh in known_terms:
            f.write(f"{esc(zh)}\t{esc(final[zh])}\t{sum(col.terms[zh].values())}\t{term_kinds(zh)}\n")
    with open(out / "glossary_new.tsv", "w", encoding="utf-8", newline="") as f:
        f.write("zh\tko\tseen\tkinds\n")
        for zh in new_terms:
            f.write(f"{esc(zh)}\t\t{sum(col.terms[zh].values())}\t{term_kinds(zh)}\n")

    untranslated = {zh: wh for zh, wh in col.missing.items() if zh not in final}
    by_area = Counter()
    with open(out / "untranslated.tsv", "w", encoding="utf-8", newline="") as f:
        f.write("zh\tchars\tlocations\n")
        for zh in sorted(untranslated, key=lambda z: sorted(untranslated[z])[0]):
            locs = sorted(untranslated[zh])
            for loc in locs:
                by_area[loc.split("/")[0]] += 1
            shown = ",".join(locs[:3]) + (f",+{len(locs) - 3}" if len(locs) > 3 else "")
            f.write(f"{esc(zh)}\t{len(zh)}\t{shown}\n")

    def ver(region):
        p = root / f"{region}/gamedata/excel/data_version.txt"
        return " / ".join(l for l in p.read_text(encoding="utf-8").splitlines() if l.split(":", 1)[-1]) if p.exists() else "?"

    by_src = Counter()
    for zh in final:
        by_src[min(col.sources[zh], key=SOURCE_PRIORITY.get)] += 1
    miss_chars = sum(len(z) for z in untranslated)

    lines = [
        "# zh→ko 사전 생성 결과",
        "",
        f"- CN 데이터: {ver('cn')}",
        f"- KR 데이터: {ver('kr')}",
        "",
        "## 사전",
        "",
        "| 항목 | 개수 |",
        "|---|---:|",
        f"| 고유 중국어 문장 (사전 항목) | {len(final):,} |",
        f"| └ UI (i18n) | {by_src['i18n']:,} |",
        f"| └ 데이터 테이블 (excel) | {by_src['excel']:,} |",
        f"| └ 스토리 | {by_src['story']:,} |",
        f"| └ 숫자 패턴으로 채움 | {by_src['template']:,} |",
        f"| └ 공식 문장 속 괄호 이름 | {by_src['bracket']:,} |",
        f"| └ 커뮤니티 표기 (extra/community_*) | {by_src['community']:,} |",
        f"| └ 직접 번역 (extra/claude_*) | {by_src['claude']:,} |",
        f"| 번역이 여러 개인 항목 (conflicts.tsv) | {len(conflicts):,} |",
        f"| 숫자 패턴 (템플릿) | {len(templates):,} |",
        f"| 용어집 (glossary.tsv) | {len(known_terms):,} |",
        f"| 새 고유명사, 번역 필요 (glossary_new.tsv) | {len(new_terms):,} |",
        "",
        "## 번역 필요 (한국 서버에 없음)",
        "",
        "| 항목 | 개수 |",
        "|---|---:|",
        f"| 고유 중국어 문장 | {len(untranslated):,} |",
        f"| 총 글자 수 | {miss_chars:,} |",
    ]
    lines += [f"| └ {area} | {n:,} |" for area, n in by_area.most_common()]
    lines += ["", "## 처리 통계", "", "| 항목 | 값 |", "|---|---:|"]
    lines += [f"| {k} | {v:,} |" for k, v in sorted(col.stats.items())]
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out / "report.md"


def write_skill_levels(root, out):
    """스킬 설명 틀별 레벨 수치 (out/skill_levels.json): {중섭 설명 틀: [{변수 이름(소문자): 값}, ...]}.

    화면 숫자만으로 채우면 OCR 오독('+30%' → '+3096')이나 안 보이는 줄 때문에 수치가 틀리므로,
    매처가 화면 숫자와 맞는 레벨을 골라 그 레벨의 정확한 값으로 채운다.
    """
    table = json.loads((root / "cn/gamedata/excel/skill_table.json").read_text(encoding="utf-8"))
    levels = defaultdict(list)
    for skill in table.values():
        for lv in skill.get("levels") or []:
            desc = lv.get("description") or ""
            if "{" not in desc:
                continue
            board = lv.get("blackboard") or []
            if isinstance(board, dict):  # arkprts로 직접 뽑으면 [{key, value}] 목록이 {key: value}로 합쳐져 나옴
                board = [{"key": k, "value": v} for k, v in board.items()]
            vals = {b["key"].lower(): b["value"] for b in board if b.get("value") is not None}
            if vals not in levels[desc]:
                levels[desc].append(vals)
    path = out / "skill_levels.json"
    path.write_text(json.dumps(levels, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return len(levels)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_root", type=Path, help="ArknightsGamedata 저장소 경로 (cn/, kr/ 포함)")
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "out")
    args = ap.parse_args()

    col = Collector()
    for name, fn in (("i18n", collect_i18n), ("excel", collect_excel), ("story", collect_story)):
        print(f"[{name}] 처리 중...", file=sys.stderr)
        fn(args.data_root, col)
    report = write_outputs(col, args.out, args.data_root)
    print(f"스킬 레벨 수치: 설명 틀 {write_skill_levels(args.data_root, args.out):,}개", file=sys.stderr)
    print(report.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
