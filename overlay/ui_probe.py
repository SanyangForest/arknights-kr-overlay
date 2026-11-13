"""UI 번들 하나에서 한자·한글이 들어 있는 MonoBehaviour 문자열 필드를 찾는다 (읽기 전용 조사용).

사용법: python ui_probe.py <번들 파일> [최대 개수]
"""

import re
import sys
from collections import Counter

import UnityPy

import lz4ak

lz4ak.register()
sys.stdout.reconfigure(encoding="utf-8")
CJK = re.compile(r"[一-鿿가-힣]")


def strings(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from strings(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node[:50]):
            yield from strings(v, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, node


env = UnityPy.load(sys.argv[1])
limit = int(sys.argv[2]) if len(sys.argv) > 2 else 30
fields, shown, failed, script_names = Counter(), 0, 0, Counter()
for obj in env.objects:
    if obj.type.name != "MonoBehaviour":
        continue
    try:
        tree = obj.read_typetree()
    except Exception:  # noqa: BLE001 - 조사용
        failed += 1
        continue
    for path, s in strings(tree):
        if CJK.search(s):
            fields[path] += 1
            if shown < limit:
                print(f"path_id={obj.path_id} field={path} text={s[:50]!r}")
                shown += 1
print("typetree 실패:", failed)
print("글자 필드:", fields.most_common(10))
