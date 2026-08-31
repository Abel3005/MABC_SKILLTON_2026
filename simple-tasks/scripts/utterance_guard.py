"""발화 검문.

항목에 들어가는 값이 사용자 발화에 근거가 있는지 검사한다.
근거가 없으면 **거부하지 않고 벗겨낸다** — 거부는 왕복을 늘리고,
카드가 뜨는 속도가 이 스킬의 체감 품질이기 때문이다.

검사는 셋이다.

- 마감: 발화에 시간 표현이 없으면 떨어뜨린다. 항상 강제한다.
- 이름: 발화에 없는 어절을 찾는다. 기본은 보고만 하고, strict일 때만 잘라낸다.
- 맥락: 같은 방식.

이름·맥락이 기본 보고인 이유는 한국어 조사·활용 때문에 오탐이 날 수 있어서다.
마감만 강제하는 것은 오탐이 거의 없고 피해가 가장 크기 때문이다 — 가짜 마감은
상태 파일에 남아 다음 세션부터 우선순위를 계속 왜곡한다.
"""

import re

# 발화에 이 중 하나라도 있어야 --deadline 을 받는다.
# 넓게 잡으면 지어낸 마감이 통과하므로 좁게 잡는다. 진짜 마감을 놓치는 쪽이
# 가짜 마감을 통과시키는 쪽보다 낫다 — 전자는 다시 말하면 되고 후자는 영구 오염이다.
_TIME_PATTERNS = [
    r"오늘|내일|모레|글피",
    r"이번\s*주|다음\s*주|담주|이번\s*달|다음\s*달",
    r"말일|월말|주말|연말|분기\s*말|이번\s*분기",
    r"[월화수목금토일]요일",
    r"오전|오후|아침|점심|저녁|새벽|정오",
    r"\d+\s*시(?!간)",              # "3시"는 잡고 "3시간"은 거른다
    r"\d+\s*월\s*\d+\s*일",
    r"\d+\s*일(?!정)",
    r"\d{1,2}\s*[:：]\s*\d{2}",
    r"까지|마감|기한|데드라인|이내",
]
_TIME_RE = re.compile("|".join(_TIME_PATTERNS))

# 비교 전에 지우는 것들. 조사까지 떼지는 않는다 — 형태소 분석 없이 조사를 떼면
# 멀쩡한 어절이 깨진다. 대신 부분 문자열로 비교해서 조사를 흡수한다.
_PUNCT_RE = re.compile(r"[\s,.·:;!?'\"()\[\]{}<>/\\|~\-_—…]+")

# 한 글자는 우연히 일치하기 쉬워 검사하지 않는다.
_MIN_TOKEN_LEN = 2


def _normalize(text):
    if not text:
        return ""
    return _PUNCT_RE.sub("", text).lower()


def has_time_expression(utterance):
    """발화에 시간 표현이 있는가."""
    if not utterance:
        return False
    return bool(_TIME_RE.search(utterance))


def unsupported_tokens(name, utterance):
    """항목 이름에서 발화에 근거가 없는 어절을 찾는다.

    어절이 발화 안에 부분 문자열로 있으면 근거가 있는 것으로 본다.
    "보고서"는 "보고서 써야하는데" 안에 있으므로 통과하고, 조사가 붙어도 흡수된다.
    """
    if not name or not utterance:
        return []

    haystack = _normalize(utterance)
    missing = []
    for token in name.split():
        norm = _normalize(token)
        if len(norm) < _MIN_TOKEN_LEN:
            continue
        if norm not in haystack:
            missing.append(token)
    return missing


def trim_name(name, utterance):
    """발화에 근거 없는 어절을 잘라낸 이름을 돌려준다.

    전부 잘려 나가면 자르지 않는다. 빈 이름보다는 거친 원본이 낫다.
    ("그거 마무리해야지" 같은 지시대명사 발화가 여기 걸리는데, 그건 항목 매칭이 풀 문제다.)
    """
    missing = set(unsupported_tokens(name, utterance))
    if not missing:
        return name, []

    kept = [t for t in name.split() if t not in missing]
    if not kept:
        return name, []
    return " ".join(kept), sorted(missing)


def filter_context(context, utterance):
    """맥락 원소 중 발화에 근거가 있는 것만 남긴다.

    Returns: (kept, dropped)
    """
    if not context:
        return [], []
    if not utterance:
        return list(context), []

    haystack = _normalize(utterance)
    kept, dropped = [], []
    for item in context:
        norm = _normalize(item)
        if len(norm) < _MIN_TOKEN_LEN or norm in haystack:
            kept.append(item)
        else:
            dropped.append(item)
    return kept, dropped


def inspect(name, utterance, deadline=None, context=None, strict=False):
    """항목 값 전체를 검문한다.

    Args:
        name: 등록하려는 항목 이름
        utterance: 사용자 발화 원문. 없으면 검문을 건너뛴다.
        deadline: 넣으려는 마감
        context: 넣으려는 맥락 리스트
        strict: True면 이름·맥락도 실제로 잘라낸다. False면 보고만 한다.

    Returns:
        {"name":..., "deadline":..., "context":[...], "report": {...}}
        report는 아무것도 걸리지 않으면 빈 dict.
    """
    result = {
        "name": name,
        "deadline": deadline,
        "context": list(context or []),
        "report": {},
    }

    if not utterance:
        return result

    # 마감 — 항상 강제한다.
    if deadline and not has_time_expression(utterance):
        result["deadline"] = None
        result["report"]["deadline_dropped"] = "발화에 시간 표현이 없다"

    # 이름 — strict일 때만 자른다.
    missing = unsupported_tokens(name, utterance)
    if missing:
        if strict:
            trimmed, cut = trim_name(name, utterance)
            if cut:
                result["name"] = trimmed
                result["report"]["name_trimmed_to"] = trimmed
        result["report"]["unsupported_tokens"] = missing

    # 맥락 — strict일 때만 자른다.
    kept, dropped = filter_context(result["context"], utterance)
    if dropped:
        if strict:
            result["context"] = kept
            result["report"]["context_dropped"] = dropped
        else:
            result["report"]["unsupported_context"] = dropped

    return result
