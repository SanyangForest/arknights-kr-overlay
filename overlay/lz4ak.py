"""명일방주 번들의 변형 LZ4 압축 해제 (UnityPy에 등록해서 사용).

일반 LZ4 블록과 같지만 각 시퀀스의 토큰 상·하위 4비트와 매치 오프셋의 바이트 순서가 바뀌어 있다.
그 둘을 원래대로 되돌린 뒤 표준 LZ4로 푼다.

방식 출처: isHarryh/Ark-Unpacker (BSD 3-Clause License).
"""

import lz4.block
from UnityPy.enums.BundleFile import CompressionFlags
from UnityPy.helpers import CompressionHelper


def _skip_length(buf, pos, end):
    """LZ4 길이 확장 바이트(0xFF가 이어지는 동안 더함)를 읽는다."""
    total = 0
    while pos < end:
        b = buf[pos]
        pos += 1
        total += b
        if b != 0xFF:
            break
    return total, pos


def decompress(data, size):
    buf = bytearray(data)
    end = len(buf)
    pos = out = 0
    while pos < end:
        token = buf[pos]
        literal, match = token & 0x0F, token >> 4      # 변형: 리터럴 길이가 하위 4비트
        buf[pos] = (literal << 4) | match                # 표준: 리터럴 길이가 상위 4비트
        pos += 1
        if literal == 0x0F:
            extra, pos = _skip_length(buf, pos, end)
            literal += extra
        pos += literal
        out += literal
        if out >= size:                                  # 마지막 시퀀스는 리터럴만 있음
            break
        buf[pos], buf[pos + 1] = buf[pos + 1], buf[pos]  # 오프셋: 빅엔디언 → 리틀엔디언
        pos += 2
        if match == 0x0F:
            extra, pos = _skip_length(buf, pos, end)
            match += extra
        out += match + 4
    return lz4.block.decompress(bytes(buf), uncompressed_size=size)


def register():
    # 명일방주는 원래 LZHAM 자리(4번)를 이 압축에 쓴다
    CompressionHelper.DECOMPRESSION_MAP[CompressionFlags.LZHAM] = decompress
