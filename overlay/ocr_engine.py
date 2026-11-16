"""게임 화면용 RapidOCR 설정 (GPU는 DirectML 사용)."""

import hashlib
from collections import OrderedDict
from pathlib import Path

import numpy as np
import onnxruntime
from rapidocr_onnxruntime import RapidOCR
from rapidocr_onnxruntime import utils as rapid_utils

import paths

DML = "DmlExecutionProvider"
# 글자 읽기 모델: PP-OCRv5 server (tools/ocr_models, 저장소에는 없음 — 없으면 RapidOCR 기본 PP-OCRv3).
# 녹화 화면 60장 비교(ocr_bench.py, 2026-11-14): 사전과 정확히 맞게 읽은 한자 v3 69.9% → v5 server 80.7%,
# 상자를 넓게(unclip 2.0) 하면 81.9%. MAA가 쓰는 PP-OCRv6 small은 77.9%, MAA의 2023년 학습판은 64.2%
# 쓸 모델은 tools/ocr_models/current.json의 "rec" (폴더 이름). 명방 전용으로 학습한 모델(akkr-*)이 더 좋으면 여기만 바꾼다.
MODELS = paths.MODELS


def current_rec():
    """(모델 파일, 글자 목록 파일) — current.json이 없거나 깨졌으면 v5server."""
    import json
    try:
        name = json.loads((MODELS / "current.json").read_text(encoding="utf-8"))["rec"]
    except (OSError, ValueError, KeyError):
        name = "v5server"
    return MODELS / name / "inference.onnx", MODELS / name / "keys.txt"


BEST_REC, BEST_KEYS = current_rec()
UNCLIP = 2.0  # 글자 상자를 넓게 잡아 첫·끝 글자가 잘리지 않게 (기본 1.6)


def _dml_session(model_path, sess_options=None, providers=None, **kwargs):
    # DirectML은 메모리 패턴 최적화와 병렬 실행을 지원하지 않음
    if sess_options is not None:
        sess_options.enable_mem_pattern = False
        sess_options.execution_mode = onnxruntime.ExecutionMode.ORT_SEQUENTIAL
    return onnxruntime.InferenceSession(model_path, sess_options=sess_options,
                                        providers=[DML, "CPUExecutionProvider"], **kwargs)


def make_ocr(gpu=True, limit_side_len=1600, det_model_path=None, rec_model_path="best", rec_keys_path=None,
             unclip_ratio=UNCLIP):
    """1600 = 게임 창 원래 크기. 1280으로 줄이면 '慢', '中等' 같은 작은 글자를 놓친다 (대신 0.1초쯤 느려짐).

    det_model_path / rec_model_path: RapidOCR 기본(PP-OCRv3) 대신 쓸 모델 (tools/ocr_models, ocr_bench.py로 비교).
      rec_model_path="best"면 BEST_REC가 있을 때 그것, ""/None이면 RapidOCR 기본.
    rec_keys_path: 글자 목록이 모델 안에 없는 읽기 모델(PP-OCRv5/v6, MAA)의 글자 목록 파일.
    """
    if rec_model_path == "best":
        rec_model_path, rec_keys_path = (BEST_REC, BEST_KEYS) if BEST_REC.exists() and BEST_KEYS.exists() else (None, None)
    if gpu and DML in onnxruntime.get_available_providers():
        rapid_utils.InferenceSession = _dml_session
    ocr = RapidOCR(**({"det_model_path": str(det_model_path)} if det_model_path else {}))
    if rec_model_path:
        rec = ocr.text_recognizer
        rec.session = rapid_utils.OrtInferSession({"model_path": str(rec_model_path), "use_cuda": False})
        keys = rec.session.get_character_list() if rec.session.have_key() else str(rec_keys_path)
        rec.postprocess_op = type(rec.postprocess_op)(keys)
    ocr.width_height_ratio = -1  # 가로로 긴 대사창도 글자 위치부터 찾도록
    ocr.use_angle_cls = False    # 게임 글자는 회전돼 있지 않음
    ocr.text_recognizer.rec_batch_num = 32  # 기본 6보다 한 번에 많이 읽는 편이 GPU에서 20~30% 빠름
    ocr.text_detector.postprocess_op.unclip_ratio = unclip_ratio
    for op in ocr.text_detector.preprocess_op:
        if hasattr(op, "limit_side_len"):
            op.limit_side_len, op.limit_type = limit_side_len, "max"  # 작은 영역을 키우지 않음
    return ocr


def providers(ocr):
    return ocr.text_detector.infer.session.get_providers()


class CachedOCR:
    """글자 읽기(rec) 결과를 글자 그림별로 기억하는 RapidOCR.

    게임 화면 인식 시간의 대부분이 줄마다 글자를 읽는 데 든다 (한 줄 약 11ms, 글이 많은 화면은 0.5초 넘게).
    화면이 조금만 바뀌어도(대사 한 줄, 2초마다 다시 확인) 전체를 다시 읽던 것을, 글자 위치 찾기(det)만 매번 하고
    잘라 낸 글자 그림이 전과 똑같으면 기억해 둔 결과를 쓴다. 결과 모양은 RapidOCR과 같다.
    """

    def __init__(self, ocr, size=3000):
        self.ocr, self.size = ocr, size
        self.cache = OrderedDict()
        self.hits = self.total = 0

    def __getattr__(self, name):
        return getattr(self.ocr, name)

    @staticmethod
    def _key(crop):
        return hashlib.blake2b(np.ascontiguousarray(crop).tobytes(), digest_size=16).digest() + bytes(str(crop.shape), "ascii")

    def __call__(self, img):
        o = self.ocr
        dt_boxes, det_elapse = o.text_detector(img)
        if dt_boxes is None or len(dt_boxes) < 1:
            return None, None
        dt_boxes = o.sorted_boxes(dt_boxes)
        crops = o.get_crop_img_list(img, dt_boxes)
        keys = [self._key(c) for c in crops]
        todo = [i for i, k in enumerate(keys) if k not in self.cache]
        rec_elapse = 0.0
        if todo:
            rec, rec_elapse = o.text_recognizer([crops[i] for i in todo])
            for i, r in zip(todo, rec):
                self.cache[keys[i]] = r
        rec_res = []
        for k in keys:
            self.cache.move_to_end(k)
            rec_res.append(self.cache[k])
        while len(self.cache) > self.size:
            self.cache.popitem(last=False)
        self.total += len(keys)
        self.hits += len(keys) - len(todo)
        boxes, recs = o.filter_boxes_rec_by_score(dt_boxes, rec_res)
        result = [[dt.tolist(), r[0], str(r[1])] for dt, r in zip(boxes, recs)]
        return (result, [det_elapse, 0.0, rec_elapse]) if result else (None, None)
