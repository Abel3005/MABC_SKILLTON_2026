"""항목 매칭.

새로 등록하려는 이름이 이미 열린 항목을 가리키는지 판정한다.
이것이 있어야 `add`가 멱등이 되고, 구체적인 이름을 쓸 자격이 정의된다 —
목록에 있는 `3분기 보고서 초안`을 지목하는 것은 회상이고, 없는 것을 만드는 것은 창작이다.

임계값은 **보수적으로(높게)** 잡는다.
놓친 매칭은 중복 항목 하나로 끝나고 눈에 보이지만,
잘못된 매칭은 사용자의 새 의도를 조용히 삼키고 보이지도 않는다.

임베딩을 쓰지 않는다. 의존성이 생기고, 같은 입력에 다른 결과가 나오면
왜 그 카드가 나왔는지 설명할 수 없게 된다.
"""

import re

_PUNCT_RE = re.compile(r"[\s,.·:;!?'\"()\[\]{}<>/\\|~\-_—…]+")

# 문자 bigram 자카드 임계값. 낮추면 오매칭이 늘어난다.
DEFAULT_THRESHOLD = 0.6

# 포함 관계로 인정하려면 짧은 쪽이 긴 쪽의 이 비율 이상이어야 한다.
# "회의"(2)가 "회의록정리하고공유"(9)에 들어 있다고 같은 항목은 아니다.
_CONTAINMENT_RATIO = 0.3

_MIN_LEN = 2


def normalize(text):
    if not text:
        return ""
    return _PUNCT_RE.sub("", text).lower()


def _bigrams(text):
    if len(text) < 2:
        return {text} if text else set()
    return {text[i:i + 2] for i in range(len(text) - 1)}


def similarity(a, b):
    """문자 bigram 자카드. 0.0 ~ 1.0"""
    ba, bb = _bigrams(normalize(a)), _bigrams(normalize(b))
    if not ba or not bb:
        return 0.0
    return len(ba & bb) / len(ba | bb)


def _contains(a, b):
    """한쪽이 다른 쪽에 충분한 비중으로 들어 있는가."""
    na, nb = normalize(a), normalize(b)
    if len(na) < _MIN_LEN or len(nb) < _MIN_LEN:
        return False

    short, long_ = (na, nb) if len(na) <= len(nb) else (nb, na)
    if short not in long_:
        return False
    return len(short) / len(long_) >= _CONTAINMENT_RATIO


def find_match(name, open_items, threshold=DEFAULT_THRESHOLD):
    """이름이 가리키는 기존 항목을 찾는다.

    Returns:
        (item, matched_by, score) 또는 None
        matched_by: "포함" | "유사"
    """
    if not name or not open_items:
        return None

    scored = []
    for item in open_items:
        other = item.get("name", "")
        if not other:
            continue
        if _contains(name, other):
            scored.append((1.0, "포함", item))
            continue
        sim = similarity(name, other)
        if sim >= threshold:
            scored.append((sim, "유사", item))

    if not scored:
        return None

    best = max(s for s, _, _ in scored)
    top = [(s, how, it) for s, how, it in scored if s == best]

    # 동점이면 재진입 메모가 있는 것을 고른다. 맥락이 가장 싸게 복구되는 항목이다.
    with_reentry = [t for t in top if t[2].get("reentry")]
    score, how, item = (with_reentry or top)[-1]

    return item, how, round(score, 3)
