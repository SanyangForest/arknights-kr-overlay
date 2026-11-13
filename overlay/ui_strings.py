"""UI 번들의 MonoBehaviour 원본 바이트에서 문자열(길이 4바이트 + UTF-8)을 뽑는다.

이 게임의 번들은 필드 정보(타입트리)가 빠져 있어 필드 이름으로 읽을 수 없으므로,
Unity 직렬화 형식(int32 길이 + 바이트 + 4바이트 정렬)을 직접 훑는다.
결과는 (오브젝트 path_id, 오브젝트 안에서 몇 번째 문자열인지, 문자열) 목록.
"""

import re
import struct

import UnityPy

import lz4ak

lz4ak.register()
CJK = re.compile(r"[一-鿿가-힣]")


def scan_strings(raw, max_len=2000):
    """raw에서 Unity 직렬화 문자열로 보이는 것을 모두 찾는다. [(바이트 위치, 문자열)]"""
    found = []
    pos, end = 0, len(raw) - 4
    while pos <= end:
        n = struct.unpack_from("<i", raw, pos)[0]
        if 1 <= n <= max_len and pos + 4 + n <= len(raw):
            chunk = raw[pos + 4:pos + 4 + n]
            try:
                s = chunk.decode("utf-8")
            except UnicodeDecodeError:
                s = None
            if s and s.isprintable() or (s and CJK.search(s)):
                found.append((pos, s))
                pos += 4 + n
                pos += (-pos) % 4
                continue
        pos += 4
    return found


def bundle_texts(path):
    """번들 하나의 한자·한글 문자열. {(path_id, n번째): 문자열}"""
    env = UnityPy.load(str(path))
    out = {}
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        raw = obj.get_raw_data()
        texts = [s for _, s in scan_strings(raw) if CJK.search(s)]
        for i, s in enumerate(texts):
            out[(obj.path_id, i)] = s
    return out


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    texts = bundle_texts(sys.argv[1])
    print(len(texts), "개")
    for (pid, i), s in list(texts.items())[:25]:
        print(pid, i, repr(s[:60]))
