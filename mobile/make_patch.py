"""사전 패치 만들기: 이전 dict.db와 새 dict.db를 표마다 비교해, 바뀐 줄만 담은 작은 SQLite 파일을 만든다.

앱은 이 패치를 받아 자기 사전에 붙이므로(Updater.kt), 사전을 고칠 때마다 80MB 앱을 다시 깔지 않아도 된다.

패치 파일 (gzip으로 압축해 dict-patch-<from>-<to>.db.gz로 올림):
  patch_info(k, v)   format, from, to, 표마다 적용한 뒤의 줄 수('rows:<표>')
  schema(name, sql)  새로 생기거나 구조가 바뀐 표 → 지우고 sql로 다시 만든 뒤 add_<표>를 모두 넣음 (sql이 NULL이면 지울 표)
  del_<표>           지울 줄의 기본 키 (새 사전에 없는 키)
  add_<표>           새로 넣거나 바뀐 줄 전체 (INSERT OR REPLACE)
  빈 del_/add_ 표는 넣지 않는다.

적용 순서 (앱과 apply_patch()가 같음): 한 트랜잭션 안에서 schema → 표마다 del → add → 줄 수와 dict_version 확인.

판 번호(dict_version)는 올린 사전의 meta 표에 찍는다 (stamp). export_dict.py의 표 구조는 바꾸지 않는다.

사용법:
  python make_patch.py OLD.db NEW.db OUTDIR      OUTDIR/dict-patch-<from>-<to>.db.gz, OUTDIR/dict-<to>.db.gz
                                                 (OLD가 '-'이면 전체 사전만). 판 번호는 두 파일의 meta.dict_version
  python make_patch.py --apply BASE.db PATCH.db.gz    BASE에 패치 적용 (시험용)
  python make_patch.py --stamp DB VERSION             DB의 meta.dict_version을 VERSION으로
"""

import gzip
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

# 패치 적용·공용 함수는 PC 오버레이의 자동 업데이트와 같이 쓴다 (overlay/dbpatch.py)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "overlay"))
from dbpatch import FORMAT, apply_patch, cols_sql, columns, count, dict_version, gunzip, gz, q, tables  # noqa: E402,F401
# 판 번호와 만든 시각만 바뀐 것은 내용이 바뀐 것으로 치지 않는다
META_VOLATILE = ("built", "dict_version")










def same_sql(a: str | None, b: str | None) -> bool:
    return " ".join((a or "").split()) == " ".join((b or "").split())






def stamp(path, version: str) -> None:
    """올리는 사전에 판 번호를 찍는다 (meta 표에 줄 하나, 표 구조는 그대로)."""
    db = sqlite3.connect(path)
    with db:
        db.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('dict_version', ?)", (version,))
    db.close()


def make_patch(old, new, out_db) -> dict:
    """old → new 패치를 out_db(압축 안 한 SQLite)로. 돌려주는 값: 표마다 (지운 줄, 넣은 줄), 'content_changed'."""
    out_db = Path(out_db)
    out_db.unlink(missing_ok=True)
    p = sqlite3.connect(out_db)
    p.execute("PRAGMA page_size = 4096")
    p.execute("ATTACH ? AS o", (str(old),))
    p.execute("ATTACH ? AS n", (str(new),))
    old_t, new_t = tables(p, "o"), tables(p, "n")
    p.execute("CREATE TABLE patch_info (k TEXT PRIMARY KEY, v TEXT)")
    p.execute("CREATE TABLE schema (name TEXT PRIMARY KEY, sql TEXT)")
    info = {"format": FORMAT, "from": dict_version(old) or "", "to": dict_version(new) or ""}
    stats: dict[str, tuple[int, int]] = {}
    changed = False
    for t in sorted(old_t.keys() | new_t.keys()):
        if t not in new_t:
            p.execute("INSERT INTO schema VALUES (?, NULL)", (t,))
            stats[t] = (count(p, "o", t), 0)
            changed = True
            continue
        cols, pk = columns(p, "n", t)
        if t not in old_t or not same_sql(old_t[t], new_t[t]):
            p.execute("INSERT INTO schema VALUES (?, ?)", (t, new_t[t]))
            p.execute(f"CREATE TABLE {q('add_' + t)} AS SELECT {cols_sql(cols)} FROM n.{q(t)}")
            changed = True
        else:
            p.execute(f"CREATE TABLE {q('del_' + t)} AS "
                      f"SELECT {cols_sql(pk)} FROM o.{q(t)} EXCEPT SELECT {cols_sql(pk)} FROM n.{q(t)}")
            p.execute(f"CREATE TABLE {q('add_' + t)} AS "
                      f"SELECT {cols_sql(cols)} FROM n.{q(t)} EXCEPT SELECT {cols_sql(cols)} FROM o.{q(t)}")
        nd = count(p, "main", "del_" + t) if ("del_" + t) in tables(p) else 0
        na = count(p, "main", "add_" + t)
        for name, n in (("del_" + t, nd), ("add_" + t, na)):
            if n == 0 and name in tables(p):
                p.execute(f"DROP TABLE {q(name)}")
        stats[t] = (nd, na)
        info["rows:" + t] = str(count(p, "n", t))
        if t == "meta" and not changed:
            ph = ",".join("?" * len(META_VOLATILE))
            if nd or (na and p.execute(f"SELECT count(*) FROM add_meta WHERE k NOT IN ({ph})", META_VOLATILE).fetchone()[0]):
                changed = True
        elif nd or na:
            changed = True
    p.executemany("INSERT INTO patch_info VALUES (?, ?)", info.items())
    p.commit()
    p.execute("DETACH o")
    p.execute("DETACH n")
    p.execute("VACUUM")
    p.close()
    stats["content_changed"] = changed
    return stats




def same_db(a, b) -> list[str]:
    """두 사전이 표·줄까지 같은지. 다른 점 목록 (같으면 빈 목록)."""
    db = sqlite3.connect(":memory:")
    db.execute("ATTACH ? AS a", (str(a),))
    db.execute("ATTACH ? AS b", (str(b),))
    ta, tb = tables(db, "a"), tables(db, "b")
    diffs = []
    for t in sorted(ta.keys() | tb.keys()):
        if t not in ta or t not in tb or not same_sql(ta[t], tb[t]):
            diffs.append(f"{t}: 표 구조 다름")
            continue
        for x, y in (("a", "b"), ("b", "a")):
            n = db.execute(f"SELECT count(*) FROM (SELECT * FROM {x}.{q(t)} EXCEPT SELECT * FROM {y}.{q(t)})").fetchone()[0]
            if n:
                diffs.append(f"{t}: {x}에만 {n}줄")
        if count(db, "a", t) != count(db, "b", t):
            diffs.append(f"{t}: 줄 수 다름 (중복 줄?)")
    db.close()
    return diffs






def build(old, new, outdir, prefix: str = "dict") -> dict:
    """패치(old가 있을 때)와 전체 사전의 .gz를 outdir에 만들고, 패치를 old 사본에 적용해 new와 같은지 확인한다.

    돌려주는 값: {'to', 'from', 'full': 경로, 'patch': 경로 또는 None, 'stats': make_patch 결과}
    """
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    to = dict_version(new)
    if not to:
        raise ValueError(f"{new}에 meta.dict_version이 없음 (--stamp로 먼저 찍기)")
    res = {"to": to, "from": None, "patch": None, "stats": None}
    if old:
        frm = dict_version(old)
        if not frm:
            raise ValueError(f"{old}에 meta.dict_version이 없음")
        res["from"] = frm
        with tempfile.TemporaryDirectory(dir=outdir) as td:
            raw = Path(td) / "patch.db"
            res["stats"] = make_patch(old, new, raw)
            patch = outdir / f"{prefix}-patch-{frm}-{to}.db.gz"
            gz(raw, patch)
            # 검증: 압축을 풀어 old 사본에 적용 → new와 같아야 함
            check, unz = Path(td) / "check.db", Path(td) / "unz.db"
            shutil.copyfile(old, check)
            gunzip(patch, unz)
            apply_patch(check, unz)
            diffs = same_db(check, new)
            if diffs:
                patch.unlink()
                raise RuntimeError("패치를 적용한 사전이 새 사전과 다름: " + "; ".join(diffs[:5]))
        res["patch"] = patch
    full = outdir / f"{prefix}-{to}.db.gz"
    gz(new, full)
    res["full"] = full
    return res


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    a = sys.argv[1:]
    if len(a) == 3 and a[0] == "--apply":
        with tempfile.TemporaryDirectory() as td:
            unz = Path(td) / "patch.db"
            gunzip(a[2], unz) if a[2].endswith(".gz") else shutil.copyfile(a[2], unz)
            apply_patch(a[1], unz)
        print(f"{a[1]}: 판 {dict_version(a[1])}")
    elif len(a) == 3 and a[0] == "--stamp":
        stamp(a[1], a[2])
        print(f"{a[1]}: 판 {a[2]}")
    elif len(a) == 3:
        res = build(None if a[0] == "-" else a[0], a[1], a[2])
        if res["patch"]:
            changes = {t: v for t, v in res["stats"].items() if t != "content_changed" and v != (0, 0)}
            print(f"패치 {res['from']} → {res['to']}: {res['patch'].stat().st_size:,} 바이트, "
                  f"바뀐 표 {changes} (내용 변경 {'있음' if res['stats']['content_changed'] else '없음'})")
        print(f"전체 사전: {res['full'].stat().st_size:,} 바이트")
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
