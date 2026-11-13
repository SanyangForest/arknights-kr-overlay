"""이미 만든 묶음의 용어집만 다시 붙인다 (항목은 그대로).

사용법: python reglossary.py content_021 content_022 ...
"""

import json
import sys

from prep_chunks import OUT, chunk_glossary, load_glossary

sys.stdout.reconfigure(encoding="utf-8")
glossary = load_glossary()
for name in sys.argv[1:]:
    path = OUT / f"{name}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["glossary"] = chunk_glossary([x["zh"] for x in payload["items"]], glossary)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(name, len(payload["glossary"]))
