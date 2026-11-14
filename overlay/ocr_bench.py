"""OCR 모델·설정 비교: 녹화한 게임 화면을 각 설정으로 읽고, 읽은 줄이 사전과 얼마나 맞는지 잰다.

사전에는 실제 게임 문장이 들어 있으므로, 읽은 줄이 사전 문장과 정확히 같으면(정확히 읽음) 좋은 OCR이다.
  정확: exact/alias/number/template/title/label 로 찾은 줄
  근사: fuzzy/segment/split/icon/prefix 등 (틀렸을 수 있음)
  없음: 한자가 있는데 못 찾은 줄
한자 글자 수로 가중해 '정확히 맞춘 한자 비율'을 주 지표로 쓴다.

사용법 (overlay 폴더에서): python ocr_bench.py [--frames N] [--configs v3,v6small,...]
"""

import argparse
import glob
import random
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from layout import merge_lines  # noqa: E402
from matcher import HAN, Matcher, normalize  # noqa: E402
from ocr_engine import make_ocr  # noqa: E402

ROOT = Path(__file__).resolve().parent
MODELS = ROOT.parent / "tools" / "ocr_models"
EXACT = {"exact", "alias", "number", "template", "title", "label", "rule"}
CONFIGS = {
    "v3": {"rec_model_path": "", "unclip_ratio": 1.6},  # RapidOCR 기본 (PP-OCRv3 det + rec)
    "maa2023": {"rec_model_path": MODELS / "maa2023/rec/inference.onnx", "rec_keys_path": MODELS / "maa2023/rec/keys.txt"},
    "v6small": {"det_model_path": MODELS / "v6small/det/inference.onnx",
                "rec_model_path": MODELS / "v6small/rec/inference.onnx", "rec_keys_path": MODELS / "v6small/rec/keys.txt"},
    "v6rec": {"rec_model_path": MODELS / "v6small/rec/inference.onnx", "rec_keys_path": MODELS / "v6small/rec/keys.txt"},
    "v5server": {"rec_model_path": MODELS / "v5server/inference.onnx", "rec_keys_path": MODELS / "v5server/keys.txt"},
    "v6det_v5rec": {"det_model_path": MODELS / "v6small/det/inference.onnx",
                    "rec_model_path": MODELS / "v5server/inference.onnx", "rec_keys_path": MODELS / "v5server/keys.txt"},
}
V5 = CONFIGS["v5server"]
# 글자 위치 찾기(det) 설정 바꿔 보기: 상자를 넓게(unclip), 큰 그림으로(limit), 흐린 글자도(thresh)
CONFIGS.update({
    "v5_unclip2": {**V5, "det": {"unclip_ratio": 2.0}},
    "v5_limit2400": {**V5, "limit_side_len": 2400},
    "v5_thresh": {**V5, "det": {"thresh": 0.2, "box_thresh": 0.4}},
    "v5_all": {**V5, "limit_side_len": 2400, "det": {"unclip_ratio": 1.8, "thresh": 0.2, "box_thresh": 0.4}},
    # 오버레이 실제 설정(상자 unclip 2.0)으로 학습한 모델
    "mobile1_unclip2": {"rec_model_path": MODELS / "akkr-mobile1/inference.onnx",
                        "rec_keys_path": MODELS / "akkr-mobile1/keys.txt", "det": {"unclip_ratio": 2.0}},
})


EVAL_SESSIONS = {"session4", "session13"}  # 학습에 안 쓴 세션 (tools/ocrtrain/make_dataset.py와 같게)
REAL_EVAL = ROOT.parent / "tools" / "ocrtrain" / "data" / "real"


def config_for(name):
    """CONFIGS 이름, 또는 tools/ocr_models의 폴더 이름(학습한 akkr-* 등: 글자 읽기 모델만 바꿈)."""
    if name in CONFIGS:
        return dict(CONFIGS[name])
    return {"rec_model_path": MODELS / name / "inference.onnx", "rec_keys_path": MODELS / name / "keys.txt"}


def frames(n, seed=7, only=None):
    """여러 녹화 세션에서 고르게 n장 (같은 세션만 몰리지 않게). only: 이 세션들에서만 (평가용)."""
    rng = random.Random(seed)
    sessions = sorted(d for d in glob.glob(str(ROOT / "captures" / "session*")) if Path(d).is_dir()
                      and not d.endswith(("_snap", "_eval")) and (not only or Path(d).name in only))
    pools = [sorted(glob.glob(f"{d}/frame_*.png")) for d in sessions]
    pools = [p for p in pools if p]
    picked = []
    while len(picked) < n and any(pools):
        for p in pools:
            if p and len(picked) < n:
                picked.append(p.pop(rng.randrange(len(p))))
    return picked


def relex(result, arr, ocr, m, lex):
    """사전 기반 재채점(lexicon.py)으로 정확히 못 찾은 줄을 고친 결과. 바꾼 (원래 글, 고친 글) 목록도."""
    out, changes = [], []
    for box, text, s in result or []:
        hit = m.lookup(text)
        if not (hit and hit[0] in EXACT):
            crop = ocr.get_crop_img_list(arr, [np.array(box, dtype=np.float32)])[0]
            fixed = lex.correct(text, crop)
            if fixed and fixed != text:
                changes.append((text, fixed))
                text = fixed
        out.append((box, text, s))
    return out, changes


def crop_accuracy(ocr, limit=3000):
    """평가용 글자 그림(make_dataset.py가 학습에 안 쓴 세션에서 자른 것)을 줄 단위로 맞힌 비율."""
    gt = REAL_EVAL / "rec_gt_eval.txt"
    if not gt.exists():
        return float("nan")
    rows = [line.rstrip("\n").split("\t", 1) for line in open(gt, encoding="utf-8")][:limit]
    crops = [np.asarray(Image.open(REAL_EVAL / p).convert("RGB")) for p, _ in rows]
    got, _ = ocr.text_recognizer(crops)
    ok = sum(normalize(t) == normalize(label) for (t, _s), (_p, label) in zip(got, rows))
    return ok / max(1, len(rows))


def score(result, w, m):
    """한 장의 (정확 한자, 근사 한자, 없음 한자, 줄 수)."""
    lines = merge_lines(result or [], w, m.can_continue, m.is_entry, lambda t: m.lookup(t) is not None)
    exact = approx = miss = 0
    for ln in lines:
        n = len(HAN.findall(ln["text"]))
        if not n:
            continue
        hit = m.lookup(ln["text"])
        if hit and hit[0] in EXACT:
            exact += n
        elif hit:
            approx += n
        else:
            miss += n
    return exact, approx, miss, len(lines)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=60)
    ap.add_argument("--configs", default=",".join(CONFIGS))
    ap.add_argument("--lexicon", action="store_true", help="사전 기반 재채점을 함께 잰다")
    ap.add_argument("--eval", action="store_true", help="학습에 안 쓴 세션(EVAL_SESSIONS)의 화면만 쓰고, 평가용 글자 그림 정답률도 잰다")
    args = ap.parse_args()
    m = Matcher(str(ROOT.parent / "out" / "dict_zh_ko.json"))
    files = frames(args.frames, only=EVAL_SESSIONS if args.eval else None)
    imgs = [np.asarray(Image.open(f).convert("RGB")) for f in files]
    print(f"화면 {len(files)}장")
    for name in args.configs.split(","):
        cfg = config_for(name)
        det_opts = cfg.pop("det", {})
        kw = {k: (v if k in ("limit_side_len", "unclip_ratio") else str(v)) for k, v in cfg.items()}
        kw.setdefault("unclip_ratio", 1.6)  # 모델끼리 비교는 상자 설정을 같게
        ocr = make_ocr(gpu=True, **kw)
        for k, v in det_opts.items():  # DB 후처리 설정 (unclip_ratio, thresh, box_thresh)
            setattr(ocr.text_detector.postprocess_op, k, v)
        ocr(imgs[0])  # 첫 실행 준비 시간은 빼고 잰다
        lex = None
        if args.lexicon:
            from lexicon import Lexicon
            lex = Lexicon(m, ocr.text_recognizer)
        tot, tot_lex, changes = np.zeros(4), np.zeros(4), []
        t = time.perf_counter()
        for arr in imgs:
            res, _ = ocr(arr)
            tot += score(res, arr.shape[1], m)
            if lex:
                fixed, ch = relex(res, arr, ocr, m, lex)
                changes += ch
                tot_lex += score(fixed, arr.shape[1], m)
        ms = (time.perf_counter() - t) * 1000 / len(imgs)
        for label, (e, a, x, n) in [(name, tot)] + ([(name + "+사전", tot_lex)] if lex else []):
            han = max(1, e + a + x)
            print(f"{label:16s} 정확 {e / han:6.1%}  근사 {a / han:6.1%}  없음 {x / han:6.1%}  (한자 {int(han)}, 줄 {int(n)})  {ms:5.0f}ms/장")
        for before, after in changes[:40]:
            print(f"    {before}  →  {after}")
        if args.eval:
            print(f"{'':16s} 평가용 글자 그림 정답률 {crop_accuracy(ocr):6.1%}")


if __name__ == "__main__":
    main()
