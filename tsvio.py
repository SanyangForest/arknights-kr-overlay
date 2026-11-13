"""TSV 칸 이스케이프. 줄바꿈·탭·CR·역슬래시를 글자 그대로 되돌릴 수 있게 한 번에 처리한다.

원문에 글자 그대로의 역슬래시(예: '\\n' 두 글자)가 들어 있는 경우가 많아서,
replace를 차례로 쓰면 '\\\\n'이 줄바꿈으로 잘못 바뀐다.
"""

import re

_ESC = {"\\": "\\\\", "\t": "\\t", "\r": "\\r", "\n": "\\n"}
_UNESC = {"\\": "\\", "t": "\t", "r": "\r", "n": "\n"}


def esc(s):
    return re.sub(r"[\\\t\r\n]", lambda m: _ESC[m.group(0)], s)


def unesc(s):
    return re.sub(r"\\(.)", lambda m: _UNESC.get(m.group(1), m.group(0)), s, flags=re.S)
