"""OCR로 읽은 중국어를 zh→ko 사전에서 찾는다.

OCR은 글자를 가끔 틀리고, 대사는 한 글자씩 나오는 도중에 찍힐 수 있으며,
스킬 설명은 사전에 {atk_scale:0%} 같은 변수로 들어 있고 화면에는 실제 숫자(350%)가 나온다.
그래서 다음 순서로 찾는다.

  1. 완전 일치
  2. 앞부분 일치 (대사가 다 나오기 전, 또는 화면에 일부만 보일 때)
  3. 숫자 틀 일치 (숫자·변수를 #로 바꿔 비교한 뒤 화면의 숫자를 번역문에 채움)
  4. 헷갈리기 쉬운 글자 교정 (干/千, 已/己 등) 후 완전 일치
  5. 비슷한 문장 (OCR 오류). 단, 숫자나 로마 숫자가 다르면 쓰지 않음
  6. 한 글자 틀린 고유명사 (로고·제목: 从林症结 → 丛林症结, 후보가 하나일 때만)
  7. 부분 번역 (숫자·기호로 나뉜 한자 덩어리가 모두 번역될 때만)

그 전에 화면별 OCR 별칭표(ocr_aliases.tsv)로 자주 틀리는 문구를 바로잡고,
아이콘+숫자 잡음(区835)은 번역하지 않는다.
"""

import bisect
import csv
import datetime
import json
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from rapidfuzz import fuzz, process

HAN = re.compile(r"[一-鿿]")
HAN_RUN = re.compile(r"[一-鿿](?:[一-鿿，、]*[一-鿿])?")
# 화면에 보이지 않는 서식 태그 (<@ba.vup>, </>, <color=...> 등)
TAG = re.compile(r"<[@$/][^<>]{0,40}>|</?color[^<>]{0,20}>|</?[ib]>|</>")
PLACEHOLDER = re.compile(r"\{[^{}]*\}")
NUMBER = re.compile(r"\d+(?:\.\d+)?%?")
DIGITS = re.compile(r"\d+")
# OCR이 헷갈리기 쉬운 문장부호를 통일
PUNCT = str.maketrans({"…": ".", "·": ".", "，": ",", "。": ".", "！": "!", "？": "?", "：": ":", "；": ";",
                       "“": '"', "”": '"', "‘": "'", "’": "'", "（": "(", "）": ")", "—": "-", "–": "-",
                       "【": "[", "】": "]", "《": "<", "》": ">", "〈": "<", "〉": ">", "×": "x"})
# 글머리표·아이콘이 글자로 잘못 읽힌 것 (◆, √, →, 돋보기 아이콘의 Q 등)
# ①②… : 방패·하트 같은 둥근 아이콘을 동그라미 숫자로 읽은 것 (①防御)
LEADING_JUNK = re.compile(r"^(?:[^\w一-鿿\[(<]|[A-Za-z](?=[一-鿿])|[①-⑳])+")
# OCR이 서로 헷갈리는 한자 (戍成: 태블릿 글꼴에서 위수 협의의 戍를 成으로 읽음, 卫成百科 → 卫戍百科)
CONFUSABLE = ["干千于", "已己巳", "未末", "人入", "土士", "日曰", "戊戍戌成", "大太犬", "王玉主", "刀力",
              "免兔", "折拆", "徒徙", "历厉", "候侯", "博傅", "摇遥瑶", "视观", "烬煜炽焖", "欸欲",
              "挑姚", "傀愧倪", "引弓", "丛从", "凛漂", "瞥警聲", "作乍", "敌文", "临高", "中史", "了子", "抉块", "瞌嗑", "物勿", "嗯想", "超招"]
# 아이콘이 한자로 잘못 읽힐 때 자주 나오는 글자 (앞에 붙음: 图较慢 → 较慢)
ICON_CHARS = set("图区回向三囗口日目田凶品吕亘目旦")
# 숫자 옆 아이콘이 한자 한 글자로 읽힌 것 (区835 = 공격력 아이콘 + 835)
NOISE_CHARS = (ICON_CHARS - set("日回三")) | set("仓四")
# 숫자 뒤에 붙어 뜻이 있는 글자 (5天, 3次, 第2…). 이 글자와 숫자만 있으면 잡음이 아님
UNIT_CHARS = set("天日时分秒次个名级层格倍张份回号期周年月第星阶")
# 끝 글자를 떼어 볼 때 제외할 조사·어기사 (开始了 → 开始 처럼 뜻이 바뀌므로)
PARTICLES = set("了的吗呢吧啊呀哦嘛么着过")
CONFUSE_MAP = {c: g for g in CONFUSABLE for c in g}
# 숫자 옆에 붙은 한 글자 단위 (부분 번역에서만 사용)
UNITS = {"秒": "초", "天": "일", "星": "성", "次": "회", "层": "층", "格": "칸", "个": "개", "名": "명",
         "级": "레벨", "点": "", "倍": "배", "张": "장", "份": "개", "回": "회", "分钟": "분", "小时": "시간"}
FUZZY_MIN_LEN = 5
FUZZY_PREFIX_MIN_LEN = 8
FUZZY_PREFIX_MAX_CANDS = 3000
# 이 종류에서 나온 이름은 영어 번역도 허용 (예: 灰烬 → Ash)
NAME_KINDS = ("story:speaker", "character_table:name", "skin_table", "handbook_info_table")
# 로고·제목처럼 짧은 고유명사는 OCR이 한 글자 틀려도 이름 목록에서 하나로 정해지면 인정 (从林症结 → 丛林症结)
TITLE_MIN_LEN, TITLE_MAX_LEN = 3, 10
# 스킨 브랜드 번호 같은 로마 숫자 (时代/XVII). 다르면 다른 항목
ROMAN = re.compile(r"(?<![A-Za-z])[IVXL]+(?![A-Za-z])")
ALIASES = Path(__file__).with_name("ocr_aliases.tsv")
# 옆으로 흘러가는 안내 문구. 화면에 일부만 보여도('指挥作战正常运行中') 전체 번역을 보여 준다
MARQUEE = Path(__file__).with_name("marquee.tsv")
# 한자 숫자. 비슷한 문장 매칭에서 이게 다르면 뜻이 다름 (周二三六日开放 ≠ 周三四六日开放)
CN_NUM = re.compile(r"[一二三四五六七八九十百千两]")
# 자원 수집 화면의 요일 표시 (周一二五六开放 → 월·화·금·토 개방). 조합이 많아 사전에 다 없음
WEEKDAY = re.compile(r"周([一二三四五六日]+)开放")
WEEKDAY_KO = dict(zip("一二三四五六日", "월화수목금토일"))
# 괄호 안 이름 (정규화 뒤 모양: 《》→<>, 【】→[], “”→"): 우편 제목처럼 이벤트 이름만 바뀌는 문장
NAME_BRACKET = re.compile(r'([<\[「"])([^<>\[\]「」"]{1,20})([>\]」"])')
# 짧은 이름 바로 뒤의 숫자 (스탯 '法术抗性15', '生命上限1888')
LABEL_NUM = re.compile(r"([一-鿿]{2,6})(\d+(?:\.\d+)?%?)")
# '라벨: 값' (값에 한자가 없음) — '库存更新倒计时：2D 06:23:15'. 사전 틀 '라벨:{0}'의 {0}에 값을 그대로 넣는다
LABEL_VALUE = re.compile(r"([^:]*[一-鿿][^:]*):([^一-鿿]+)")
# 기반시설 방 이름 옆 레벨 막대(▮▮▮)를 OCR이 '000'·'!'·'…'로 읽은 것 ('制造站000' → 制造站)
ROOM_BARS = re.compile(r"(.+?(?:站|室|舍|中枢))[0OIl|!.]+")
# 화면 끝에서 잘린 요일 표시: 오른쪽 끝 '周一四五['(뒤가 잘림), 왼쪽 끝 '日六日开放'(앞이 잘림, 첫 글자는 반쯤 잘린 글자)
WEEKDAY_HEAD = re.compile(r"周([一二三四五六日]+)\W*")
WEEKDAY_TAIL = re.compile(r"\W*([一二三四五六日]+)开放")
PROGRESS = re.compile(r"\((\d+/\d+)\)$")
PURCHASE = re.compile(r"[A-Za-z]?\d*购买(.{2,20}?)\?")
# 통합 전략 테마 로고. 꾸민 글씨라 OCR이 매번 다르게 읽음 ('影雅狐钻', '影马彤狐钻' = 傀影与猩红孤钻)
LOGO_TITLES = ["傀影与猩红孤钻", "水月与深蓝之树", "探索者的银凇止境", "萨卡兹的无终奇语", "岁的界园志异", "沉沦者的黑流树海",
               "视相博物馆:演化"]  # 사전 키는 문장부호를 바꿔 둔 모양 (：→:).   홈 오른쪽 위 이벤트 배너 ('提想博物境演化', '携想博物增演化', '摆想博物培演化'…)
# 통합 전략 결과 화면의 기록 줄: 您 Dr.이름 使用 분대 历时 00时40分21秒 于 날짜 시각 驻足在 엔딩 노드
EXPIRE = re.compile(r"(\d{4})-(\d{1,2})-(\d{2,})日后过期")
# 이벤트 화면 '作战结束时间 2026/11/22 03:59': 정규화로 공백이 빠져 날짜와 시각이 붙음 (2026/11/2203:59)
# 폰(ML Kit)은 옆의 남은 날수까지 한 줄로 읽음 ('…03:59剩余17天', 剩이나 날수가 빠지기도 함), 束을 東으로 읽기도 함
DATE_TIME = re.compile(r"(\d{4}/\d{1,2}/\d{2})(\d{2}:\d{2})")
# 번역 결과에서 날짜와 시각이 붙은 것 ('만료 시간 2026/11/2303:59'): OCR이 사이 공백을 빼먹음
DATETIME_GLUED = re.compile(r"(\d{4}[/-]\d{1,2}[/-]\d{2})(\d{2}:\d{2})")
# '+'/'-' 뒤 숫자의 '%'를 '9'로 읽은 자리 ('+259' = '+25%'). 뒤에 숫자가 이어지지 않는 9만
PERCENT_AS_9 = re.compile(r"([+\-＋－]\d+)9(?![\d.:/])")
# 채용·헤드헌팅 결과 화면의 '직군 + 이름' (사이에 직군 아이콘이 '之'·'A' 등으로 읽힘). 한국어 직군은 공개모집 태그 표기
CLASS_KO = {"先锋": "뱅가드", "近卫": "가드", "重装": "디펜더", "狙击": "스나이퍼", "术师": "캐스터", "医疗": "메딕",
            "辅助": "서포터", "特种": "스페셜리스트"}
CLASS_NAME = re.compile(rf"^({'|'.join(CLASS_KO)})(之|A|Δ|△|图)?(.{{1,12}})$")
CLASS_LOOSE = re.compile(r"^([一-鿿]{2})(之|A|Δ|△|图)?([一-鿿]{1,12})$")
END_TIME = re.compile(r"(作战)?结[束東]时间:?(\d{4})/(\d{1,2})/(\d{2})(\d{2}):?(\d{2})(?:剩?余(\d*)天?)?\W*")
MAIL_COUNT = re.compile(r"\d+/\d+[天未]")
# 우편 보관 기간으로 흔한 날수
MAIL_VALIDITY = (7, 10, 14, 15, 30, 60, 90, 180, 365)


def split_expire(y, mo, rest, today=None):
    """'2026-8-1' + '88'처럼 붙어 읽힌 받은 날짜와 남은 날수를 나눈다.

    일이 한 자리인지 두 자리인지는 글자만으로 알 수 없으므로, (오늘 + 남은 날수) - 받은 날짜 = 보관 기간이
    흔한 값(30일, 180일 …)에 가까운 쪽을 고른다. ('2026-8-188': 8-1 + 88일 → 179일, 8-18 + 8일 → 82일)
    """
    today = today or datetime.date.today()
    best = None
    for k in (2, 1):
        day, left = rest[:k], rest[k:]
        if not left or left.startswith("0"):
            continue
        try:
            got = datetime.date(int(y), int(mo), int(day))
        except ValueError:
            continue
        if got > today + datetime.timedelta(days=1):
            continue
        total = (today - got).days + int(left)
        score = min(abs(total - v) / v for v in MAIL_VALIDITY)
        if best is None or score < best[0]:
            best = (score, f"{y}-{mo}-{day}", left)
    return best and best[1:]
IS_RECORD = re.compile(r"您(.+?)使用(.+?)历时(\d+)时(\d+)分(\d+)秒于(\d{4}/\d{1,2}/\d{1,2})(\d{1,2}:\d{2}:\d{2})驻足在(.+)")


def object_particle(word):
    """목적격 조사: 받침 있으면 '을', 없으면 '를' (한글이 아니면 '을(를)')."""
    c = word.rstrip()[-1:] or "가"
    if not "가" <= c <= "힣":
        return "을(를)"
    return "을" if (ord(c) - 0xAC00) % 28 else "를"
# 시계 아이콘이 '?', '¥', 'Q', '9.', '0.' 등으로 읽혀 남은 날짜·시간 앞에 붙은 것.
# 소수점 앞자리를 떼는 건 날짜만 (게임에 '2.5天'은 없지만 '2.5秒'는 있음)
ICON_NUM = re.compile(r"[A-Za-z¥?.]+(\d+(?:天|小时|分钟|分|秒|日))|\d[.,](\d+(?:天|日))")


def normalize(s):
    s = TAG.sub("", s).replace("\\n", "").replace("\n", "")
    # OCR은 줄표 '——'를 한자 '一一'로 읽는다. 사전 쪽도 같이 바꾸므로 양쪽이 맞음
    s = s.replace("一一", "——")
    s = s.translate(PUNCT)
    return re.sub(r"[\s.]+", lambda m: "." if "." in m.group() else "", s)


def skeleton(s):
    """숫자와 변수를 #로 바꾼 틀. 사전 원문은 {변수}, 화면은 실제 숫자이므로 둘 다 #가 된다."""
    s = PLACEHOLDER.sub("#", normalize(s))
    return NUMBER.sub("#", s)


def display(ko):
    """번역문에서 화면에 안 보일 서식 태그를 지운다."""
    return TAG.sub("", ko).replace("\\n", "\n").strip()


VAR = re.compile(r"\{(-)?([^:{}]+)(?::([^{}]*))?\}")


def format_value(token, vals):
    """스킬 설명 변수({atk:0%}, {-move_speed:0%}, {duration:0.0}, {attack@sp})를 게임처럼 숫자로. 값이 없으면 None."""
    m = VAR.fullmatch(token)
    if not m:
        return None
    neg, key, fmt = m.group(1), m.group(2).lower(), m.group(3) or ""
    if key not in vals:
        return None
    v = Decimal(repr(float(vals[key])))
    if neg:
        v = -v
    pct = fmt.endswith("%")
    if pct:
        v, fmt = v * 100, fmt[:-1]
    if fmt:
        dec = len(fmt.split(".")[1]) if "." in fmt else 0
        text = str(v.quantize(Decimal(1).scaleb(-dec), rounding=ROUND_HALF_UP))
    else:
        text = str(int(v)) if v == v.to_integral_value() else format(v.normalize(), "f")
    return text + ("%" if pct else "")


def ocr_matches(expected, got):
    """화면에서 읽은 숫자가 데이터 수치와 같은가. OCR이 '%'를 9·96으로 읽은 것도 같다고 본다 (+30% → +3096, 380% → 3809%)."""
    e, g = expected.rstrip("%").lstrip("+-"), got.rstrip("%").lstrip("+-")
    return g == e or (expected.endswith("%") and g.startswith(e) and g[len(e):] in ("9", "96", "6"))


def digits(s):
    return DIGITS.findall(s)


BRACKETED = re.compile(r"<([^<>]*)>|\[([^\[\]]*)\]")


def brackets_agree(a, b):
    """괄호 안 고유명사가 다르면 다른 문장 (예: <视相博物馆:演化>活动已开放 ≠ <살카즈…>活动已开放)."""
    xa = ["".join(g) for g in BRACKETED.findall(a)]
    xb = ["".join(g) for g in BRACKETED.findall(b)]
    if len(xa) != len(xb):
        return not xa and not xb
    return all(fuzz.ratio(p, q) >= 90 for p, q in zip(xa, xb))


def romans(s):
    return ROMAN.findall(s)


def edge_of(box, width, margin=8):
    """글자 상자가 화면 왼쪽·오른쪽 끝에 닿았으면 'left'/'right' (가로로 넘기는 목록에서 잘린 카드 글자)."""
    if box[0] <= margin:
        return "left"
    if box[2] >= width - margin:
        return "right"
    return None


def load_glossary(glossary_path):
    """(영어 번역을 허용할 이름들, 제목 교정에 쓸 고유명사들, 오퍼레이터 이름들)."""
    names, titles, ops = set(), set(), set()
    if glossary_path and Path(glossary_path).exists():
        with open(glossary_path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
                if any(k in row["kinds"] for k in NAME_KINDS):
                    names.add(row["zh"])
                if "character_table:name" in row["kinds"]:
                    ops.add(normalize(row["zh"]))
                if row["ko"] and TITLE_MIN_LEN <= len(row["zh"]) <= TITLE_MAX_LEN:
                    titles.add(row["zh"])
    return names, titles, ops


def load_aliases(path=ALIASES):
    """화면별 OCR 오독 → 원래 문구. {(화면, 오독): 원문}. 화면 '*'는 모든 화면."""
    aliases = {}
    if path.exists():
        with open(path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader((l for l in f if not l.startswith("#")), delimiter="\t", quoting=csv.QUOTE_NONE):
                aliases[(row["screen"], normalize(row["ocr"]))] = row["zh"]
    return aliases


class Matcher:
    def __init__(self, dict_path, glossary_path=None):
        raw = json.loads(Path(dict_path).read_text(encoding="utf-8"))
        names, titles, self.op_names = load_glossary(glossary_path or Path(dict_path).with_name("glossary.tsv"))
        self.aliases = load_aliases()
        # 스킬 설명 틀별 레벨 수치 (build_dict.py가 skill_table에서 만듦): 화면 숫자와 맞는 레벨의 정확한 값으로 채운다
        levels = Path(dict_path).with_name("skill_levels.json")
        self.levels = ({normalize(k): v for k, v in json.loads(levels.read_text(encoding="utf-8")).items()}
                       if levels.exists() else {})
        settings = Path(__file__).with_name("settings.json")
        self.nickname = json.loads(settings.read_text(encoding="utf-8-sig")).get("nickname") if settings.exists() else None
        self.marquee = []
        if MARQUEE.exists():
            with open(MARQUEE, encoding="utf-8", newline="") as f:
                self.marquee = [normalize(r["zh"]) for r in csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE)]
        self.table = {}
        self.skel = {}
        self.skel_multi = set()
        self.skipped_english = 0
        for zh, ko in raw.items():
            # 한국 서버가 영어로 둔 항목은 데이터 짝이 어긋났을 가능성이 커서 이름과 약어(HP, SP 등)만 허용
            if (ko.isascii() and re.search(r"[A-Za-z]", ko) and zh not in names
                    and not re.fullmatch(r"[A-Z]{1,4}", ko.strip())):
                self.skipped_english += 1
                continue
            n = normalize(zh)
            if n and (n not in self.table or len(zh) < len(self.table[n][0])):
                self.table[n] = (zh, ko)
            # 원문에 '#'이 있으면 틀(숫자 자리 #)과 헷갈리므로 뺀다. 색 태그(<color=#898989FF>)의 #은 화면에 없으니 괜찮음
            if "#" not in TAG.sub("", zh) and (PLACEHOLDER.search(zh) or NUMBER.search(zh)):
                k = skeleton(zh)
                if HAN.search(k) and k in self.skel and self.skel[k][0] != zh:
                    self.skel_multi.add(k)  # 숫자만 다른 같은 문장이 여럿 (재능의 잠재·정예화 단계별 수치 등)
                if HAN.search(k) and (k not in self.skel or len(zh) < len(self.skel[k][0])):
                    self.skel[k] = (zh, ko)
        self._add_line_entries(raw)
        self.keys = sorted(self.table)
        self.skel_keys = sorted(self.skel)
        # 이름이 들어가는 틀 ('{0}的基础证章' = 오퍼레이터 이름의 기본 배지): 고정 글자(2자 이상 한자) → 틀
        self.name_tpl_head, self.name_tpl_tail = {}, {}
        for k in self.keys:
            if k.count("{0}") == 1 and "{1}" not in k:
                lit = k[3:] if k.startswith("{0}") else k[:-3] if k.endswith("{0}") else ""
                if len(HAN.findall(lit)) >= 2 and "{" not in lit:
                    (self.name_tpl_head if k.startswith("{0}") else self.name_tpl_tail)[lit] = k
        self.by_len = {}
        for k in self.keys:
            self.by_len.setdefault(len(k), []).append(k)
        self.rev_keys = sorted(k[::-1] for k in self.keys)
        self.skel_by_len = {}
        for k in self.skel_keys:
            self.skel_by_len.setdefault(len(k), []).append(k)
        self._index_titles(titles)

    def skel_by_len_range(self, lo, hi):
        for n in range(lo, hi + 1):
            yield from self.skel_by_len.get(n, ())

    @staticmethod
    def _cut_weekday(q, edge):
        """화면 끝에서 잘린 요일 표시 → '월·목·금…' / '…토·일 개방'. 보이는 요일만 옮긴다.

        요일은 늘 월→일 순서이므로, 순서가 어긋나는 글자(왼쪽 끝의 반쯤 잘린 글자)부터는 버린다.
        """
        order = "一二三四五六日"
        if edge == "right":
            m = WEEKDAY_HEAD.fullmatch(q)
            days = m and m.group(1)
            if not days or any(order.index(a) >= order.index(b) for a, b in zip(days, days[1:])):
                return None
            return "·".join(WEEKDAY_KO[c] for c in days) + "…"
        m = WEEKDAY_TAIL.fullmatch(q)
        if not m:
            return None
        days = ""
        for c in reversed(m.group(1)):
            if days and order.index(c) >= order.index(days[0]):
                break
            days = c + days
        return "…" + "·".join(WEEKDAY_KO[c] for c in days) + " 개방"

    def _edge(self, q, edge, confusable=True):
        """화면 가장자리에서 잘린 글자 (edge='left': 앞이 잘림, 'right': 뒤가 잘림).

        잘린 조각으로 끝나거나(시작하는) 사전 문구 중 가장 짧은 것이 하나뿐일 때만 인정.
        못 찾으면 헷갈리는 글자를 하나씩 바꿔 본다 (왼쪽 끝 '勿运送' = 货物运送).
        """
        # 요일 표시(周一四五…)는 잘린 뒤쪽 요일을 알 수 없음
        if len(q) < 3 or not HAN.search(q) or q.startswith("周") or q.endswith("开放"):
            return None
        keys, probe = (self.rev_keys, q[::-1]) if edge == "left" else (self.keys, q)
        lo = bisect.bisect_left(keys, probe)
        hi = bisect.bisect_left(keys, probe + "￿", lo, min(len(keys), lo + 2000))
        cands = [k[::-1] if edge == "left" else k for k in keys[lo:hi]]
        # 잘린 부분에 한자 숫자가 있으면 뜻이 달라지므로 (周一四五 → 周一四五日? 周一四五六?) 숫자가 같은 것만
        cands = [k for k in cands if k != q and len(k) <= len(q) + 4 and CN_NUM.findall(k) == CN_NUM.findall(q)]
        if not cands:
            if confusable:
                for i, ch in enumerate(q):
                    for c in CONFUSE_MAP.get(ch, ""):
                        hit = c != ch and self._edge(q[:i] + c + q[i + 1:], edge, False)
                        if hit:
                            return hit
            return None
        shortest = min(len(k) for k in cands)
        best = [k for k in cands if len(k) == shortest]
        if len(best) != 1:
            return None
        return "edge", *self.table[best[0]], 90

    def _index_titles(self, titles):
        """한 글자가 틀리거나(바뀜) 빠진 OCR 결과로 고유명사를 찾기 위한 색인.

        바뀜: 각 자리를 '?'로 가린 열쇠 → 이름들.  빠짐: 한 글자를 뺀 열쇠 → 이름들.
        """
        self.title_sub, self.title_del = {}, {}
        for zh in titles:
            n = normalize(zh)
            if n not in self.table or not HAN.search(n):
                continue
            for i in range(len(n)):
                self.title_sub.setdefault(n[:i] + "?" + n[i + 1:], set()).add(n)
                self.title_del.setdefault(n[:i] + n[i + 1:], set()).add(n)

    def _title(self, q):
        """한 글자만 다른 고유명사가 하나뿐이면 그것. 없거나 여럿이면 None."""
        if not TITLE_MIN_LEN <= len(q) <= TITLE_MAX_LEN or len(HAN.findall(q)) < len(q) - 1:
            return None
        found = set()
        if len(q) >= 4:  # 3글자 이름은 한 글자 바뀜까지 허용하면 엉뚱한 이름이 너무 많이 걸림
            for i in range(len(q)):
                found |= self.title_sub.get(q[:i] + "?" + q[i + 1:], set())
        found |= self.title_del.get(q, set())
        found.discard(q)
        if len(found) != 1:
            return None
        key = found.pop()
        return "title", *self.table[key], 95

    def _name_template(self, q):
        """'维什戴尔的基础证章' → 틀 '{0}的基础证章' + 이름 '维什戴尔'. 이름은 사전에 그대로(또는 한 글자 오독) 있어야 함."""
        for n in range(2, min(len(q) - 1, 16)):
            for lit, name, tpl in ((q[-n:], q[:-n], self.name_tpl_head), (q[:n], q[n:], self.name_tpl_tail)):
                key = tpl.get(lit)
                if key and HAN.search(name):
                    hit = self._whole(name) or self._title(name)
                    if hit and hit[0] in ("exact", "title", "confuse"):
                        return "template", key, self.table[key][1].replace("{0}", display(hit[2])), 90
        return None

    def _class_name(self, q):
        """채용·헤드헌팅 결과의 '직군 + 이름' ('先锋芬', 직군 아이콘이 끼어 '先锋之芬'·'术师A杜林') → '뱅가드 팽'.

        이름 쪽은 사전에 그대로(또는 한 글자 오독) 있어야 한다. 연출 중이라 직군 두 글자 중 하나가 틀리기도 함
        ('洗锋芬' = 先锋芬, '木师杜林' = 术师杜林, '辅图梓兰' = 辅助 + 梓兰): 나머지 한 글자로 직군이 하나로 정해지고
        이름이 오퍼레이터일 때만.
        """
        m = CLASS_NAME.match(q)
        if m:
            cls, name = m.group(1), m.group(3)
        else:
            m = CLASS_LOOSE.match(q)
            # 앞 두 글자가 그 자체로 낱말이면 직군 오독이 아님 ('医生凯尔希')
            if not m or m.group(1) in self.table:
                return None
            cands = [c for c in CLASS_KO if (c[0] == m.group(1)[0]) != (c[1] == m.group(1)[1])]
            if len(cands) != 1:
                return None
            cls, name = cands[0], m.group(3)
        hit = self._whole(name) or self._title(name) or self._op_name(name)
        # 이름은 오퍼레이터여야 함 ('医疗炎'의 炎은 세력 이름, '先锋中'의 中은 '보통')
        if (hit and hit[0] in ("exact", "title", "confuse", "op")
                and (normalize(hit[1]) in self.op_names or not self.op_names)):
            return "rule", q, f"{CLASS_KO[cls]} {display(hit[2])}", 90
        return None

    def _op_name(self, q):
        """세 글자 이상 오퍼레이터 이름의 한 글자 오독 ('泡普泉' = 泡普卡). 맞는 이름이 하나뿐일 때만."""
        if len(q) < 3:
            return None
        if not hasattr(self, "op_sub"):
            self.op_sub = {}
            for n in self.op_names:
                if len(n) >= 3 and n in self.table:
                    for i in range(len(n)):
                        self.op_sub.setdefault(n[:i] + "?" + n[i + 1:], set()).add(n)
        found = set()
        for i in range(len(q)):
            found |= self.op_sub.get(q[:i] + "?" + q[i + 1:], set())
        if len(found) != 1:
            return None
        return "op", *self.table[found.pop()], 85

    def _bracket_template(self, q):
        """'<冻土与火种>网页活动奖励发放' → 틀 '<{0}>网页活动奖励发放' + 이름 번역 (사전에 없는 이름은 원문 그대로).

        앞에 붙은 연도('2026「感谢庆典」直播奖励')는 틀의 {1}에 넣는다.
        """
        m = NAME_BRACKET.search(q)
        if not m:
            return None
        key = q[:m.start(2)] + "{0}" + q[m.end(2):]
        year = re.match(r"\d+", key)
        for k, num in ((key, None), ("{1}" + key[year.end():], year.group()) if year else (None, None)):
            if k and k in self.table:
                inner = self._whole(m.group(2)) or self._title(m.group(2))
                name = display(inner[2]) if inner and inner[0] in ("exact", "title", "confuse") else m.group(2)
                ko = self.table[k][1].replace("{0}", name)
                return "template", k, ko.replace("{1}", num) if num else ko, 90
        return None

    def _label_value(self, q):
        """'라벨: 값'에서 값이 숫자·시간처럼 한자가 없는 것. 사전 틀 '라벨:{0}'이 있으면 그 틀에, 없으면 '라벨 번역: 값'."""
        m = LABEL_VALUE.fullmatch(q)
        if not m:
            return None
        # 정규화로 지워진 띄어쓰기 복원: '2D06:23:15' → '2D 06:23:15'
        label, value = m.group(1), re.sub(r"([A-Za-z])(\d)", r"\1 \2", m.group(2).strip())
        for key in (label + ":{0}", label + ":{1}"):
            if key in self.table:
                zh, ko = self.table[key]
                return "split", zh, display(ko).replace("{0}", value).replace("{1}", value), 90
        if label in self.table:
            zh, ko = self.table[label]
            return "split", zh, f"{display(ko)}: {value}", 90
        return None

    def _label_then_text(self, q, screen):
        """라벨과 설명이 한 줄로 붙어 읽힌 것 ('获得方式)-视相博物馆：…'). 앞 라벨과 뒤 문장을 따로 번역해 잇는다."""
        for i in range(min(8, len(q) - 3), 1, -1):
            head, rest = q[:i], q[i:]
            if head in self.table and not HAN.match(rest):
                rest = LEADING_JUNK.sub("", rest)
                hit = self.lookup(rest, screen) if HAN.search(rest) else None
                if hit:  # 원문 칸에는 뒤 문장의 사전 원문 (줄바꿈된 꼬리 판단에 씀)
                    return "split", hit[1], f"{display(self.table[head][1])} {hit[2]}", hit[3]
        return None

    @staticmethod
    def is_tail_of(text, zh_full):
        """text가 윗줄에서 이미 통째로 번역한 문장(zh_full)의 일부인가 (줄바꿈된 꼬리 '名人堂珐琅（1/8）')."""
        q = normalize(text)
        q = PROGRESS.sub("", q)
        if not HAN.search(q):
            # 한자 없이 숫자·괄호만 남은 마지막 줄 ('…降低至攻击力的5' + '0%）')
            # 숫자만 있는 줄(스탯 값 '25')은 별개 글자일 수 있으므로 문장부호가 섞인 것만
            return len(q) >= 2 and re.search(r"[^0-9A-Za-z]", q) is not None and normalize(zh_full).endswith(q)
        full = normalize(zh_full)
        if len(HAN.findall(q)) < 2:
            # 한 글자만 남은 마지막 줄 ('…今天想买点什么' + '吗？'): 윗문장이 그것으로 끝날 때만
            return len(q) >= 2 and full.endswith(q)
        if q in full:
            return True
        # 숫자·변수를 #로 바꿔 비교 (사전 문장은 '{n}', 화면은 '3'). OCR 오타가 섞인 꼬리 줄('颧外' ↔ '额外')은
        # 같은 길이 구간과 아주 비슷하면 꼬리 (폰과 같음)
        sq, sfull = skeleton(q), skeleton(full)
        if sq in sfull:
            return True
        if len(HAN.findall(q)) < 6:
            return False
        n = len(sq)
        return any(fuzz.ratio(sq, sfull[i:i + n]) >= 75 for i in range(max(1, len(sfull) - n + 1)))

    def is_entry(self, text):
        """사전에 (거의) 그대로 있는 완결된 문구인가 (줄 합치기에서 따로 완결된 두 줄을 붙이지 않으려고).

        OCR이 문장부호를 조금 다르게 읽은 것('“冬雷轰鸣，…”')도 같은 길이의 사전 문장과 95점 이상이면 인정.
        """
        q = normalize(text)
        if q in self.table:
            return True
        # 앞의 아이콘이 한자로 읽힌 라벨 ('三攻击速度')
        stripped = LEADING_JUNK.sub("", q)
        if stripped in self.table or (len(stripped) >= 3 and stripped[0] in ICON_CHARS and stripped[1:] in self.table):
            return True
        if len(q) < 8:
            return False
        cands = [k for n in range(len(q) - 1, len(q) + 2) for k in self.by_len.get(n, ())]
        best = process.extractOne(q, cands, scorer=fuzz.ratio, score_cutoff=95) if cands else None
        return best is not None

    @staticmethod
    def is_noise(text):
        """아이콘이 한자 한 글자로 읽히고 나머지는 숫자뿐인 것 (区835, 仓16, 四2026/10/27). 번역할 것이 없음.

        5天처럼 뜻이 있는 단위는 아님: 아이콘으로 자주 잘못 읽히는 글자일 때만.
        """
        q = normalize(text)
        han = HAN.findall(q)
        if len(han) != 1 or not (q[0] == han[0] or q[-1] == han[0]) or not DIGITS.search(q):
            return False
        # 전투 배치 칸의 직군 아이콘 + 코스트 ('公12', '么16', '来16')도 같은 경우. 날짜·횟수 같은 단위 글자는 제외
        return han[0] in NOISE_CHARS or (han[0] not in UNIT_CHARS and q[0] == han[0] and q[1:].isdigit())

    def _add_line_entries(self, raw):
        """줄바꿈이 있는 항목은 줄마다 따로도 찾을 수 있게 한다 (예: '17:03\\n雾气蔓延…'의 둘째 줄만 화면에 보임).

        중국어와 한국어의 줄 수가 같을 때만 줄끼리 짝짓고, 이미 있는 항목은 덮어쓰지 않는다.
        """
        for zh, ko in raw.items():
            if "\n" not in zh and "\\n" not in zh:
                continue
            zl = [s for s in re.split(r"\\n|\n", zh) if s.strip()]
            kl = [s for s in re.split(r"\\n|\n", ko) if s.strip()]
            if len(zl) < 2 or len(zl) != len(kl):
                continue
            for z, k in zip(zl, kl):
                n = normalize(z)
                if len(n) >= 4 and HAN.search(n) and n not in self.table:
                    self.table[n] = (z, k)

    # ------------------------------------------------------------ 줄 합치기 판단

    def can_continue(self, text, fuzzy=True):
        """text가 사전의 어떤 문장의 앞부분인가 (줄바꿈된 문장 합치기 판단용). fuzzy면 OCR 오타는 조금 허용."""
        if self.nickname and self.nickname in text:
            text = text.replace(self.nickname, "{@nickname}")
        q = LEADING_JUNK.sub("", normalize(text))
        for keys, k in ((self.keys, q), (self.skel_keys, skeleton(q))):
            i = bisect.bisect_left(keys, k)
            if i < len(keys) and keys[i].startswith(k):
                return True
        return fuzzy and self._fuzzy_prefix(q) is not None

    def _bucket(self, keys, head):
        """head로 시작하는 사전 키들 (첫 몇 글자가 같은 후보)."""
        lo = bisect.bisect_left(keys, head)
        hi = bisect.bisect_left(keys, head + "￿")
        return keys[lo:min(hi, lo + FUZZY_PREFIX_MAX_CANDS)]

    def _fuzzy_prefix(self, q, cutoff=85):
        """첫 4글자가 같은 사전 문장 중 앞부분이 q와 가장 비슷한 것. (키, 테이블) 또는 None."""
        if len(q) < FUZZY_PREFIX_MIN_LEN:
            return None
        best = None
        for keys, table, k in ((self.keys, self.table, q), (self.skel_keys, self.skel, skeleton(q))):
            # 첫 4글자에 OCR 혼동 글자가 있으면 후보를 못 찾으므로 바꿔 본 머리글자도 쓴다 (欲,好困 → 欸,好困)
            starts = {k[:4]} | {k[:i] + c + k[i + 1:4] for i, ch in enumerate(k[:4])
                                for c in CONFUSE_MAP.get(ch, "") if c != ch}
            heads = set(starts)
            for h in starts:
                # 넷째 글자를 잘못 읽어도('你误入子一个…' → 了) 앞 세 글자로 후보를 찾는다
                heads.add(h[:3])
                # 머리의 말줄임표를 OCR이 빼먹은 대사('嗯……博士是' → '嗯博士是', 嗯을 想으로 읽은 '想博士是')
                heads |= {h[:i] + "." + h[i:3] for i in (1, 2)}
            for key in (key for head in heads for key in self._bucket(keys, head)):
                score = fuzz.ratio(k, key[:len(k)])
                if score >= cutoff and (best is None or score > best[0]):
                    best = (score, key, table)
        return best

    # ------------------------------------------------------------ 찾기

    @staticmethod
    def _prefix(keys, q):
        i = bisect.bisect_left(keys, q)
        return keys[i] if i < len(keys) and keys[i].startswith(q) else None

    @staticmethod
    def _fill_levels(plain_zh, ko, q, sets):
        """스킬 설명: 게임 데이터의 레벨별 수치 중 화면 숫자와 맞는 레벨로 채운다 (화면 숫자는 레벨을 고르는 데만 씀).

        보이는 숫자로 레벨을 하나로 못 좁히면(줄이 잘려 숫자가 안 보임 등) 레벨마다 다른 자리만 '?'.
        보이는 숫자가 어느 레벨과도 하나도 안 맞으면 None (같은 문장 틀의 다른 스킬일 수 있어 원래 방식으로).
        """
        pairs = list(zip(re.findall(r"\{[^{}]*\}|\d+(?:\.\d+)?%?", plain_zh), NUMBER.findall(q)))
        good = sets
        if pairs:
            scored = []
            for vals in sets:
                hit = miss = 0
                for tok, got in pairs:
                    want = format_value(tok, vals) if tok.startswith("{") else tok
                    if want is None:
                        continue
                    if ocr_matches(want, got):
                        hit += 1
                    else:
                        miss += 1
                scored.append((miss, hit, vals))
            least = min(miss for miss, _, _ in scored)
            good = [vals for miss, _, vals in scored if miss == least]
            if least and not any(hit for miss, hit, _ in scored if miss == least):
                return None

        def put(m):
            tok = m.group()
            if tok == "{@nickname}":
                return tok
            found = {format_value(tok, vals) for vals in good} - {None}
            return found.pop() if len(found) == 1 else "?"

        return PLACEHOLDER.sub(put, ko)

    def _fill(self, zh, ko, q, strict=False, ambiguous=False):
        """숫자 틀로 찾은 문장: zh의 숫자·변수 자리에 화면의 숫자를 넣어 ko를 만든다.

        strict: 원문에 그대로 쓰인 숫자를 화면 숫자로 바꿀 수 없으면 None (비슷한 문장의 다른 날짜·수치를 그대로 보여 주지 않게).
        """
        # 서식 태그 이름의 숫자(<$ba.dt.burning2>)를 수치로 세지 않게 먼저 지운다 (어차피 화면에는 안 보임)
        ko = TAG.sub("", ko)
        sets = self.levels.get(normalize(zh))
        if sets:
            filled = self._fill_levels(TAG.sub("", zh), ko, q, sets)
            if filled is not None:
                return filled
        # 아이콘이 0으로 읽혀 '05天'처럼 앞에 붙는 경우가 있어 정수의 앞자리 0은 뗀다 (같은 자리 번역이 5일↔05일로 흔들림).
        # 단, 시각·날짜 안의 숫자(12:00, 03:59, 2026/10/05)는 그대로
        def keep_zero(m):
            before = q[m.start() - 1] if m.start() > 0 else ""
            after = q[m.end()] if m.end() < len(q) else ""
            return (before and before in ":/-") or (after and after in ":/") or "." in m.group()

        matches = list(NUMBER.finditer(q))
        values = [m.group() if keep_zero(m) else (m.group().lstrip("0") or "0") for m in matches]
        plain_zh = TAG.sub("", zh)
        tokens = re.findall(r"\{[^{}]*\}|\d+(?:\.\d+)?%?", plain_zh)
        named, named_at = {}, {}
        for i, (tok, val) in enumerate(zip(tokens, values)):
            if tok.startswith("{"):
                named[tok] = val
                named_at[tok] = i
        # 한섭은 변수에 단위까지 넣어 쓰기도 함: 중섭 '{0}天后开放' / 한섭 '{0} 후 개방' (게임이 {0}에 '1일'을 넣음).
        # 화면에서 읽은 건 숫자뿐이므로, 원문 변수 뒤의 단위가 번역 틀에 없으면 한국어 단위를 붙인다
        units = sorted(UNITS, key=len, reverse=True)
        unit_after = {}
        for tok in named:
            after = plain_zh[plain_zh.find(tok) + len(tok):]
            unit = next((u for u in units if after.startswith(u) and UNITS[u]), None)
            # 원문 틀에는 단위가 없고 화면 숫자 뒤에만 있는 것 ('1天后解锁' ↔ 틀 '{0}后解锁', 한섭 '{0} 후 개방')
            if not unit:
                q_after = q[matches[named_at[tok]].end():]
                unit = next((u for u in units if q_after.startswith(u) and UNITS[u]), None)
            if unit:
                unit_after[tok] = UNITS[unit]

        def put(m):
            tok = m.group()
            if tok == "{@nickname}":  # 박사 이름은 lookup()이 넣는다
                return tok
            val = named.get(tok, "?")
            unit = unit_after.get(tok)
            # 번역 틀에서 변수 바로 뒤에 한글이 붙어 있으면('{0}초간', '{0}명') 이미 단위가 있는 것
            nxt = TAG.sub("", ko[m.end():])[:1]
            if unit and not ("가" <= nxt <= "힣"):
                val += unit
            return val

        out = PLACEHOLDER.sub(put, ko)
        # 원문에 그대로 쓰인 숫자가 화면과 다르면(같은 틀의 다른 수치) 순서대로 바꿈
        literal_zh = [t for t in tokens if not t.startswith("{")]
        seen = [(t, v) for t, v in zip(tokens, values) if not t.startswith("{")]
        literal_q = [v for _, v in seen]
        differs = [t for t, _ in seen] != literal_q  # 화면에 보이는 만큼의 숫자가 원문과 다름
        n_out = len(NUMBER.findall(out))
        if differs and len(literal_zh) == len(literal_q) == n_out:
            it = iter(literal_q)
            out = NUMBER.sub(lambda m: next(it), out)
        elif differs and strict:
            return None
        elif ((differs or ambiguous or skeleton(zh) in self.skel_multi) and not strict
              and len(literal_q) < len(literal_zh) == n_out):
            # 앞부분만 보이는 문장 ('攻击力+25%，攻击速度' ↔ 사전 '攻击力+4%，攻击速度+4', 재능 잠재 단계별 문장):
            # 보이는 숫자는 화면 값, 안 보이는 뒤 숫자는 단계마다 다를 수 있으므로 '?'
            it = iter(literal_q)
            out = NUMBER.sub(lambda m: next(it, None) or "?" + ("%" if m.group().endswith("%") else ""), out)
        return out

    def _whole(self, q, min_prefix=6, fuzzy_cutoff=85):
        if q in self.table:
            return "exact", *self.table[q], 100
        if len(q) >= min_prefix:
            k = self._prefix(self.keys, q)
            # 숫자 비교는 정규화한 키로 (원문의 서식 태그 이름 <$ba.dt.burning2>의 숫자를 세지 않게, 폰과 같음)
            if k and digits(k)[:len(digits(q))] == digits(q):
                zh, ko = self.table[k]
                # 같은 앞부분으로 시작하는 문장이 여럿이면(재능 잠재 단계별 수치) 안 보이는 뒤 숫자는 어느 것인지 모름
                i = bisect.bisect_left(self.keys, q)
                ambiguous = k != q and i + 1 < len(self.keys) and self.keys[i + 1].startswith(q)
                # 줄이 잘려 앞부분만 보이는 스킬 설명: 번역 틀의 변수({same_wave_repeat_damage_scale:0%})를 날것으로 보이지 않게
                if PLACEHOLDER.sub("", ko.replace("{@nickname}", "")) != ko.replace("{@nickname}", ""):
                    ko = self._fill(zh, ko, q)
                elif ambiguous and NUMBER.search(TAG.sub("", ko)):
                    ko = self._fill(zh, ko, q, ambiguous=True)
                return "prefix", zh, ko, 100
        sq = skeleton(q)
        if "#" in sq and HAN.search(sq):
            k = sq if sq in self.skel else (self._prefix(self.skel_keys, sq) if len(sq) >= min_prefix else None)
            if k:
                zh, ko = self.skel[k]
                # 앞부분만 맞는 틀이 둘 이상이면(재능 잠재 단계별 '4(+1)초' 등) 안 보이는 숫자는 어느 것인지 모름
                i = bisect.bisect_left(self.skel_keys, sq)
                ambiguous = k != sq and i + 1 < len(self.skel_keys) and self.skel_keys[i + 1].startswith(sq)
                return "number", zh, self._fill(zh, ko, q, ambiguous=ambiguous), 100
        # 헷갈리는 글자 교정 (한 글자만 바꿔 보고, 후보가 하나일 때만)
        if len(q) <= 12:
            found = {q[:i] + c + q[i + 1:] for i, ch in enumerate(q) for c in CONFUSE_MAP.get(ch, "") if c != ch}
            found = [v for v in found if v in self.table]
            if len(found) == 1:
                return "confuse", *self.table[found[0]], 99
        if len(q) >= FUZZY_MIN_LEN:
            span = max(2, len(q) // 5)
            cands = [k for n in range(len(q) - span, len(q) + span + 1) for k in self.by_len.get(n, ())]
            best = process.extractOne(q, cands, scorer=fuzz.ratio, score_cutoff=fuzzy_cutoff) if cands else None
            # OCR 문구가 사전 문장보다 꽤 길면 뒷부분 뜻이 빠지므로 쓰지 않음 (精英阶段1后解锁 ≠ 精英阶段1后)
            if (best and len(best[0]) >= len(q) * 0.85
                    and digits(self.table[best[0]][0]) == digits(q) and romans(best[0]) == romans(q)
                    and CN_NUM.findall(best[0]) == CN_NUM.findall(q) and brackets_agree(q, best[0])):
                return "fuzzy", *self.table[best[0]], round(best[1])
        # 숫자가 든 짧은 문구의 OCR 오타 ('剩余气收置角色：9' → 剩余可放置角色：{n}): 숫자를 #로 바꾼 틀끼리 비교
        sq = skeleton(q)
        if "#" in sq and len(sq) >= FUZZY_MIN_LEN:
            span = max(1, len(sq) // 5)
            cands = [k for k in self.skel_by_len_range(len(sq) - span, len(sq) + span)]
            # 짧은 틀은 두 글자만 틀려도 점수가 78쯤으로 떨어진다 ('剩余气收置角色：#'). 숫자 자리 수까지 같아야 하므로 조금 낮춰 줌
            cutoff = 75 if len(sq) <= 12 else fuzzy_cutoff
            best = process.extractOne(sq, cands, scorer=fuzz.ratio, score_cutoff=cutoff) if cands else None
            # 낮춘 기준으로 잡혔으면 첫 글자와 끝 두 글자는 같아야 함 (精英阶段#后更新 ≠ 精英阶段#后解锁)
            if best and best[1] < fuzzy_cutoff:
                a, b = sq.replace("#", ""), best[0].replace("#", "")
                if not a or not b or a[0] != b[0] or a[-2:] != b[-2:]:
                    best = None
            if best and sq.count("#") == best[0].count("#") and CN_NUM.findall(best[0]) == CN_NUM.findall(sq):
                zh, ko = self.skel[best[0]]
                filled = self._fill(zh, ko, q, strict=True)
                if filled is not None:
                    return "fuzzy_number", zh, filled, round(best[1])
        # 비슷한 앞부분 (설명문 일부만 보이는데 OCR 오타가 섞인 경우)
        fp = self._fuzzy_prefix(q, cutoff=88)
        # 짧은 글(라벨 두 개가 합쳐진 '移动速度三攻击速度')이 아주 긴 문장의 앞부분으로 잡히면 엉뚱한 설명문이 나옴
        if fp and len(q) < 10 and len(fp[1]) > 2 * len(q):
            fp = None
        if fp:
            score, key, table = fp
            zh, ko = table[key]
            if table is self.skel:
                filled = self._fill(zh, ko, q, strict=True)
                return ("fuzzy_prefix", zh, filled, round(score)) if filled is not None else None
            if digits(zh)[:len(digits(q))] == digits(q):
                if PLACEHOLDER.sub("", ko.replace("{@nickname}", "")) != ko.replace("{@nickname}", ""):
                    ko = self._fill(zh, ko, q)
                return "fuzzy_prefix", zh, ko, round(score)
        return None

    def _split_words(self, run, max_word=6, max_parts=4):
        """run을 사전에 있는 2글자 이상 단어들로 빈틈없이 나눈다 (가장 적은 조각). 번역 목록 또는 None."""
        n = len(run)
        if n < 4:
            return None
        best = [None] * (n + 1)  # best[i] = run[:i]까지의 최소 조각 목록
        best[0] = []
        for i in range(n):
            if best[i] is None or len(best[i]) >= max_parts:
                continue
            for j in range(i + 2, min(n, i + max_word) + 1):
                word = run[i:j]
                if word in self.table and (best[j] is None or len(best[j]) > len(best[i]) + 1):
                    best[j] = best[i] + [display(self.table[word][1])]
        return best[n] if best[n] and len(best[n]) >= 2 else None

    def _segments(self, text):
        """한자 덩어리를 각각 번역하고 숫자·기호는 그대로 둔다. 모든 덩어리가 번역될 때만 성공."""
        parts = []
        pos = 0
        # 아주 짧은 문구(예: 아이콘+'慢')는 한 글자 단어도 사전에서 찾음. 긴 문구에서는 오인식 위험이 커서 안 함
        short = len(re.sub(r"\s", "", text)) <= 3
        for m in HAN_RUN.finditer(text):
            parts.append(("raw", text[pos:m.start()]))
            run = m.group()
            if run in UNITS and not short:
                parts.append(("unit", UNITS[run]))
            else:
                hit = self._whole(normalize(run), min_prefix=99) if len(run) >= 2 or short else None
                if hit:
                    parts.append(("ko", display(hit[2])))
                else:
                    words = self._split_words(normalize(run))  # 띄어쓰기 없이 붙은 태그 (支援减速 → 支援+减速)
                    if not words:
                        return None
                    parts.append(("ko", " ".join(words)))
            pos = m.end()
        parts.append(("raw", text[pos:]))
        if not any(kind != "raw" for kind, _ in parts):
            return None
        # 번역문 앞뒤로만 띄어쓰기. 단위는 숫자에 붙임 (22秒 → 22초)
        ko = ""
        for kind, part in parts:
            if not part:
                continue
            if kind == "raw":
                part = part.translate(PUNCT)
                if ko and part[0] == ko[-1] and not part[0].isalnum():
                    part = part[1:]  # 번역문 끝의 ':'와 원문의 '：'가 겹침
                if not part:
                    continue
                if ko and part[0].isalnum() and not ko[-1].isspace():
                    ko += " "
            elif kind == "ko" and ko and not ko[-1].isspace() and ko[-1] not in "([<":
                ko += " "
            ko += part
        return "segment", text, re.sub(r"\s+", " ", ko).strip(), 100

    def lookup(self, text, screen=None, edge=None):
        """(방식, 원문, 번역, 점수) 또는 None.

        대사에 박사 이름이 들어가면('果然yagirimu博士的指挥') 사전의 '{@nickname}' 자리로 바꿔 찾고,
        번역에는 다시 이름을 넣는다 (settings.json의 nickname).
        """
        if self.nickname and self.nickname in text:
            text = text.replace(self.nickname, "{@nickname}")
        hit = self._lookup(text, screen, edge)
        if not hit and "卫成" in text:
            # 위수 협의의 '卫戍'를 OCR이 '卫成'으로 읽음 ('成功卫成', '最佳卫成奖杯')
            hit = self._lookup(text.replace("卫成", "卫戍"), screen, edge)
        if (not hit or hit[0] in ("number", "segment", "fuzzy_number")) and PERCENT_AS_9.search(text):
            # OCR이 '+' 뒤 숫자의 '%'를 '9'로 읽은 경우 ('攻击力+259' = '攻击力+25%'). 원래대로는 숫자 틀에 맞아
            # '+259'로 나오므로, '%'로 바꾼 쪽이 사전에 그대로 있거나 '%'가 든 틀에 맞을 때 그쪽을 쓴다
            fixed = self._lookup(PERCENT_AS_9.sub(r"\1%", text), screen, edge)
            if fixed and (fixed[0] in ("exact", "alias") or (fixed[0] in ("number", "template", "fuzzy_number") and "%" in fixed[1])):
                hit = fixed
        if hit and DATETIME_GLUED.search(hit[2]):
            hit = (hit[0], hit[1], DATETIME_GLUED.sub(r"\1 \2", hit[2]), hit[3])
        if hit and "{@nickname}" in hit[2]:
            hit = (hit[0], hit[1], hit[2].replace("{@nickname}", self.nickname or "박사"), hit[3])
        # OCR이 날짜와 시각 사이 공백을 빼먹어 번역에 '2026/11/2303:59'처럼 붙어 나온 것을 띄운다
        if hit and DATE_TIME.search(hit[2]):
            hit = (hit[0], hit[1], DATE_TIME.sub(r"\1 \2", hit[2]), hit[3])
        return hit

    def _lookup(self, text, screen=None, edge=None):
        """(방식, 원문, 번역, 점수) 또는 None. 번역은 화면 표시용으로 태그를 지운 상태.

        screen: screens.json의 화면 id. 그 화면(또는 모든 화면)의 OCR 별칭표에 있으면 원래 문구로 바꿔 찾는다.
        edge: 글자 상자가 화면 왼쪽/오른쪽 끝에 닿아 잘렸을 수 있으면 'left'/'right' (edge_of 참고).
        """
        q = normalize(text)
        if not HAN.search(q) or self.is_noise(text):
            return None
        # 우편함 '4/100  未读邮件 0': OCR이 '未'를 한 번 더 '天'으로 읽어 개수 뒤에 붙임. 숫자는 번역할 게 없으므로
        # 그리지 않는다 (그리면 '4/100 일' 상자가 옆 '미확인' 상자를 가림)
        if screen == "mail" and MAIL_COUNT.fullmatch(q):
            return None
        # 시계 아이콘 등이 글자 하나로 읽혀 '숫자+단위' 앞에 붙은 것 (Q5天, ¥5天 → 5天)
        icon_num = ICON_NUM.fullmatch(q)
        if icon_num:
            q = text = icon_num.group(1) or icon_num.group(2)
        alias = self.aliases.get((screen, q)) or self.aliases.get(("*", q))
        if alias and alias.startswith("="):  # 그 화면에서만 다르게 옮길 말 (설정의 关闭 = 끄기)
            return "alias", text, alias[1:], 100
        if alias:
            hit = self.lookup(alias)
            return ("alias", *hit[1:]) if hit else None
        bars = ROOM_BARS.fullmatch(q)
        if bars and q not in self.table and bars.group(1) in self.table:
            return ("icon", *self.table[bars.group(1)], 95)
        wd = WEEKDAY.fullmatch(q)
        if wd and q not in self.table:
            return "rule", text, "·".join(WEEKDAY_KO[c] for c in wd.group(1)) + " 개방", 100
        cut_days = edge and self._cut_weekday(q, edge)
        if cut_days:
            return "edge", text, cut_days, 90
        rec = IS_RECORD.fullmatch(q)
        if rec:
            dr, squad, h, mi, s, date, clock, place = rec.groups()
            squad_ko = (self._whole(squad) or (None, None, squad))[2]
            place_hit = self._whole(place) or self._title(place)
            place_ko = display(place_hit[2]) if place_hit else place
            return ("rule", text, f"{dr} 님은 {display(squad_ko)}로 {h}시간 {mi}분 {s}초 동안 탐험하여 "
                                  f"{date} {clock}, {place_ko}에서 발걸음을 멈췄습니다", 95)
        # 우편 유효기간: 날짜와 남은 날수가 붙어 읽힘 ('2026-10-2626日后过期' = 2026-10-26 + 26日后过期)
        end = END_TIME.fullmatch(q)
        if end:
            op, y, mo, d, hh, mm, left = end.groups()
            return "rule", text, f"{'작전 ' if op else ''}종료 시간 {y}/{mo}/{d} {hh}:{mm}" + (f"  {left}일 남음" if left else ""), 95
        exp = EXPIRE.fullmatch(q)
        if exp:
            split = split_expire(*exp.groups())
            if split:
                date, left = split
                return "rule", text, f"{date} {left}일 후 유효기간 만료", 95
        # 통합 전략 상점의 구매 확인 ('8购买钝爪-典训？': 앞 숫자는 가격, 뒤는 물건 이름)
        buy = PURCHASE.fullmatch(q)
        if buy and q not in self.table:
            item = self._whole(buy.group(1))
            if item:
                name = display(item[2])
                return "rule", text, f"{name}{object_particle(name)} 구매하시겠습니까?", 95
        # 끝에 붙은 진행도 (훈장 획득 방법 '…获得主题场景演化（0/1）'): 떼고 찾은 뒤 다시 붙인다
        progress = PROGRESS.search(q)
        if progress and q not in self.table and HAN.search(q[:progress.start()]):
            hit = self.lookup(q[:progress.start()], screen, edge)
            if hit:
                return hit[0], hit[1], f"{hit[2]} ({progress.group(1)})", hit[3]
        # 글머리표·아이콘 잡음을 먼저 떼고 찾는다 (잡음이 붙은 채로 비슷한 문장을 찾으면 엉뚱한 게 걸림)
        stripped = LEADING_JUNK.sub("", q)
        hit = self._whole(stripped) if HAN.search(stripped) else None
        # 이름만 사전에 그대로 있으면 '이름 숫자'. 비슷한 문장('法术抗性+15')으로 찾으면 부호가 붙어 버림
        label_num = LABEL_NUM.fullmatch(stripped)
        if label_num and label_num.group(1) in self.table and (not hit or hit[0].startswith("fuzzy")):
            zh, ko = self.table[label_num.group(1)]
            hit = ("number", zh, f"{ko} {label_num.group(2)}", 95)
        # 비슷한 문장으로 찾은 게 이름 자리({0})가 있는 틀이면, 괄호 안 이름을 채워 넣는 쪽으로 다시 찾는다
        if hit and hit[0].startswith("fuzzy") and "{0}" in hit[1]:
            hit = self._bracket_template(stripped) or hit
        if not hit and stripped != q:
            hit = self._whole(q)
        if not hit:
            # 아이콘 규칙보다 먼저: '先锋芬'의 '芬'을 끝에 붙은 아이콘으로 떼어 버리지 않게
            hit = self._class_name(stripped)
        if not hit:
            # 한 글자만 다른 고유명사가 하나뿐이면 글자를 떼는 규칙보다 먼저 ('纯烟艾雅法拉' = 纯烬艾雅法拉,
            # '御银灰' = 凛御银灰: 떼면 이격 오퍼레이터가 원래 오퍼레이터 이름으로 바뀜)
            hit = self._title(stripped)
            # 단, 맨 앞·맨 끝 한 글자만 바뀐 이름이고 그 글자를 떼면 사전 말이면 아이콘 쪽 ('口阿米娅'는 小阿米娅가 아니라
            # 아이콘 + 阿米娅, '无人机梦'은 无人机群이 아니라 无人机 + 아이콘)
            if hit and len(hit[1]) == len(stripped) and (
                    (normalize(hit[1])[1:] == stripped[1:] and stripped[1:] in self.table)
                    or (normalize(hit[1])[:-1] == stripped[:-1] and stripped[:-1] in self.table)):
                hit = None
        if not hit and len(stripped) >= 3:
            # 앞의 아이콘이 한자로 잘못 읽힌 경우 (三攻击速度, 图较慢): 첫 글자를 빼고 완전 일치만 인정.
            # 위수 협의 카드의 맹약 아이콘은 읽을 때마다 다른 글자가 됨 (乡迅捷, 温坚守, 必不屈) → 세 글자면 아무 글자나
            rest = stripped[1:]
            # 세 글자인데 끝 글자를 떼도 낱말이면('专精金' = 专精 + 금 아이콘) 앞 글자 아이콘으로 단정하지 않음
            if rest in self.table and (len(stripped) >= 4 or stripped[0] in ICON_CHARS
                                       or (not self._prefix(self.keys, stripped) and stripped[:-1] not in self.table)):
                hit = ("icon", *self.table[rest], 99)
        if not hit and len(stripped) >= 3 and stripped[-1] not in PARTICLES:
            # 뒤의 아이콘이 한자로 읽힌 경우 (专精金, 管理十): 끝 글자를 빼고 완전 일치만 인정
            rest = stripped[:-1].rstrip()
            if rest in self.table:
                hit = ("icon", *self.table[rest], 99)
        if not hit and len(stripped) >= 5:
            # 제목 양옆 장식선·아이콘이 글자로 읽힌 경우 (很一休息一下, 一休息一下十): 앞 두 글자·뒤 한 글자까지 떼어 봄
            # 떼는 앞 두 글자가 낱말이면 장식이 아님 ('教官奥尔佳的信物', '来自企鹅物流', '全员信赖提升')
            for a in (1, 2):
                for b in (0, 1):
                    rest = stripped[a:len(stripped) - b]
                    if a == 2 and stripped[:2] in self.table:
                        continue
                    if (a + b) >= 2 and len(rest) >= 3 and rest in self.table:
                        hit = ("icon", *self.table[rest], 98)
                        break
                if hit:
                    break
        if not hit:
            hit = self._title(stripped)
        if not hit and edge:
            hit = self._edge(stripped.rstrip("[(<"), edge)
        if not hit and len(stripped) >= 8:
            # 긴 문장의 앞 몇 글자가 아이콘·따옴표 쪽으로 떨어져 나간 것 ('“希望”' + '可用于招募和进阶干员')
            hit = self._edge(stripped, "left")
        if not hit:
            hit = self._name_template(stripped)
        if not hit:
            hit = self._bracket_template(stripped)
        if not hit:
            hit = self._label_value(stripped)
        if not hit:
            hit = self._label_then_text(stripped, screen)
        if not hit and 3 <= len(stripped) <= 8 and len(HAN.findall(stripped)) == len(stripped):
            # 테마 로고: 첫 글자와 끝 글자가 들어 있고 두 글자 이상 겹치는 로고가 하나뿐이면
            # 첫 글자가 틀려도 끝 글자가 맞고 로고 글자 절반 이상(3자 이상)이 들어 있으면 같은 로고
            logos = [t for t in LOGO_TITLES if stripped[-1] in t and len(stripped) <= len(t) + 1 and t in self.table
                     and ((stripped[0] in t or any(c in t for c in CONFUSE_MAP.get(stripped[0], "")))
                          and len(set(stripped) & set(t)) >= 2
                          or len(stripped) >= len(t) - 2 and len(set(stripped) & set(t)) >= max(3, len(t) / 2))]
            if len(logos) == 1:
                hit = ("logo", *self.table[logos[0]], 80)
        if not hit and len(stripped) >= 3:
            # 흘러가는 안내 문구의 조각 (OCR이 헷갈리는 글자 하나는 바꿔 봄: 乍战 → 作战)
            variants = {stripped} | {stripped[:i] + c + stripped[i + 1:] for i, ch in enumerate(stripped)
                                     for c in CONFUSE_MAP.get(ch, "") if c != ch}
            for full in self.marquee:
                if full in self.table and any(v in full for v in variants):
                    hit = ("marquee", *self.table[full], 90)
                    break
        if hit:
            how, zh, ko, score = hit
            return how, zh, display(ko), score
        raw = LEADING_JUNK.sub("", TAG.sub("", text).strip())
        return self._segments(raw)
