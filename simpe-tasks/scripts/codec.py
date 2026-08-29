"""항목 텍스트 필드를 base64로 인코딩/디코딩한다.

모델이 상태 파일을 직접 열어도 항목 이름을 읽을 수 없게 만든다.
"""

import base64

_TEXT_FIELDS = ("name", "reentry", "deadline", "muted")


def encode_text(value):
    """문자열을 base64로 인코딩한다. None이면 None 반환."""
    if value is None:
        return None
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def decode_text(value):
    """base64 문자열을 디코딩한다. None이면 None 반환."""
    if value is None:
        return None
    return base64.b64decode(value.encode("ascii")).decode("utf-8")


def encode_item(item):
    """항목 dict의 텍스트 필드를 인코딩한다."""
    encoded = dict(item)
    for field in _TEXT_FIELDS:
        if field in encoded and encoded[field] is not None:
            encoded[field] = encode_text(encoded[field])
    return encoded


def decode_item(item):
    """항목 dict의 텍스트 필드를 디코딩한다."""
    decoded = dict(item)
    for field in _TEXT_FIELDS:
        if field in decoded and decoded[field] is not None:
            decoded[field] = decode_text(decoded[field])
    return decoded
