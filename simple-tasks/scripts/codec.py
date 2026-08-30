"""항목 텍스트 필드를 base64로 인코딩/디코딩한다.

모델이 상태 파일을 직접 열어도 항목 이름을 읽을 수 없게 만든다.
"""

import base64

_TEXT_FIELDS = ("name", "reentry", "deadline", "muted", "spawned_by")

# 엣지 필드. 항목 이름·맥락 이름이 그대로 들어가므로 원소 단위로 인코딩한다.
_LIST_FIELDS = ("context", "blocks")


def encode_list(values):
    """문자열 리스트를 원소별로 인코딩한다. None이면 None 반환."""
    if values is None:
        return None
    return [encode_text(v) for v in values]


def decode_list(values):
    """인코딩된 리스트를 디코딩한다. None이면 None 반환."""
    if values is None:
        return None
    return [decode_text(v) for v in values]


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
    """항목 dict의 텍스트 필드와 엣지 필드를 인코딩한다."""
    encoded = dict(item)
    for field in _TEXT_FIELDS:
        if field in encoded and encoded[field] is not None:
            encoded[field] = encode_text(encoded[field])
    for field in _LIST_FIELDS:
        if field in encoded and encoded[field] is not None:
            encoded[field] = encode_list(encoded[field])
    return encoded


def decode_item(item):
    """항목 dict의 텍스트 필드와 엣지 필드를 디코딩한다."""
    decoded = dict(item)
    for field in _TEXT_FIELDS:
        if field in decoded and decoded[field] is not None:
            decoded[field] = decode_text(decoded[field])
    for field in _LIST_FIELDS:
        if field in decoded and decoded[field] is not None:
            decoded[field] = decode_list(decoded[field])
    return decoded
