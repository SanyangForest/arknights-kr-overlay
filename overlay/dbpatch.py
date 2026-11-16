"""SQLite 사전 패치 적용 (휴대폰 앱 Updater.kt, mobile/make_patch.py와 같은 형식).

패치 파일: patch_info(k, v) · schema(name, sql) · del_<표> · add_<표>. 적용은 한 트랜잭션 안에서
schema → 표마다 del → add → 줄 수와 meta.dict_version 확인 (어긋나면 되돌림).
PC 배포판의 자동 업데이트(updater.py)와 패치를 만드는 mobile/make_patch.py가 함께 쓴다.
"""

import gzip
import shutil
import sqlite3
from pathlib import Path

FORMAT = "1"


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def cols_sql(cols) -> str:
    return ", ".join(q(c) for c in cols)


def tables(db: sqlite3.Connection, schema: str = "main") -> dict:
    """표 이름 → CREATE 문 (sqlite_ 내부 표 빼고)."""
    return dict(db.execute(
        f"SELECT name, sql FROM {schema}.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))


def columns(db: sqlite3.Connection, schema: str, table: str):
    """(모든 열, 기본 키 열). 기본 키가 없으면 모든 열을 키로 본다."""
    rows = db.execute(f"PRAGMA {schema}.table_info({q(table)})").fetchall()
    cols = [r[1] for r in rows]
    pk = [r[1] for r in sorted((r for r in rows if r[5]), key=lambda r: r[5])]
    return cols, pk or cols


def count(db: sqlite3.Connection, schema: str, table: str) -> int:
    return db.execute(f"SELECT count(*) FROM {schema}.{q(table)}").fetchone()[0]


def apply_patch(base, patch_db) -> None:
    """base(SQLite)에 압축 푼 패치를 적용한다."""
    db = sqlite3.connect(base, isolation_level=None)
    try:
        db.execute("ATTACH ? AS p", (str(patch_db),))
        info = dict(db.execute("SELECT k, v FROM p.patch_info"))
        if info.get("format") != FORMAT:
            raise ValueError(f"모르는 패치 형식: {info.get('format')}")
        have = dict(db.execute("SELECT v, 1 FROM meta WHERE k='dict_version'"))
        if info["from"] and info["from"] not in have:
            raise ValueError(f"패치 시작 판 {info['from']}이 사전 판과 다름")
        names = tables(db, "p")
        db.execute("BEGIN")
        try:
            for name, sql in db.execute("SELECT name, sql FROM p.schema").fetchall():
                db.execute(f"DROP TABLE IF EXISTS main.{q(name)}")
                if sql:
                    db.execute(sql)
            targets = sorted({n[4:] for n in names if n.startswith(("del_", "add_"))})
            for t in targets:
                if "del_" + t in names:
                    keys, _ = columns(db, "p", "del_" + t)
                    lhs = q(keys[0]) if len(keys) == 1 else f"({cols_sql(keys)})"
                    db.execute(f"DELETE FROM main.{q(t)} WHERE {lhs} IN (SELECT {cols_sql(keys)} FROM p.{q('del_' + t)})")
                if "add_" + t in names:
                    cols, _ = columns(db, "p", "add_" + t)
                    db.execute(f"INSERT OR REPLACE INTO main.{q(t)} ({cols_sql(cols)}) "
                               f"SELECT {cols_sql(cols)} FROM p.{q('add_' + t)}")
            for k, v in info.items():
                if k.startswith("rows:") and count(db, "main", k[5:]) != int(v):
                    raise ValueError(f"{k[5:]} 줄 수가 {count(db, 'main', k[5:])} (기대 {v})")
            got = db.execute("SELECT v FROM meta WHERE k='dict_version'").fetchone()
            if info["to"] and (not got or got[0] != info["to"]):
                raise ValueError(f"패치 뒤 판 번호가 {got} (기대 {info['to']})")
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
        db.execute("DETACH p")
    finally:
        db.close()


def gunzip(src, dst) -> None:
    with gzip.open(src, "rb") as g, open(dst, "wb") as f:
        shutil.copyfileobj(g, f, 1 << 20)


def gz(src, dst, level: int = 9) -> None:
    """mtime=0으로 압축 (같은 내용이면 같은 파일)."""
    with open(src, "rb") as f, open(dst, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=level, mtime=0) as g:
        shutil.copyfileobj(f, g, 1 << 20)


def dict_version(path):
    if not Path(path).exists():
        return None
    db = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    try:
        row = db.execute("SELECT v FROM meta WHERE k='dict_version'").fetchone()
        return row[0] if row else None
    finally:
        db.close()
