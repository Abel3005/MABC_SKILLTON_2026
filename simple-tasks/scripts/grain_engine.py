"""영역별 숙련도와 카드 굵기(grain).

`size_calibrator`는 **전역** 보정이다 — "이 사람은 크게 못 한다" 하나뿐이라
"보고서는 익숙한데 보험 청구는 처음"을 구분하지 못한다. 그런데 잘못 쪼갤 확률은
사람이 아니라 **사람 × 영역**에 붙는다. 숙련한 영역에 잘게 쪼갠 지시를 주면
수행이 오히려 떨어지고(expertise reversal), 낯선 영역에 굵게 주면 착수를 못 한다.

영역 키는 `context` 노드를 그대로 쓴다. `실적보고서.xlsx`·`보험앱`·`어머니` 같은
것들이고, 이미 항목에 붙어 있으며 **사용자가 발화한 것에서만 온다.** 영역을
따로 분류하지 않는 이유가 여기 있다 — 분류를 만들면 그 분류를 추론해야 하고,
추론한 영역은 틀린 엣지와 같은 종류의 조용한 오염이 된다.

**여기서 만드는 것은 카드 하나의 굵기뿐이다.** 계층 트리를 만들지 않는다.
트리를 만들면 그것을 보여주고 싶어지고, 보여주면 훑을 거리가 된다.
"""

SIZE_ORDER = ["large", "medium", "small", "minimal"]

# 이 영역에서 이만큼 실패했으면 낯선 영역으로 본다.
_UNFAMILIAR_FAILS = 2

# 이만큼 해냈고 "너무 큼"이 없으면 숙련 영역으로 본다.
# 3으로 잡은 것은 우연을 거르되 영영 굵어지지 않는 일이 없게 하는 선이다.
_FAMILIAR_DONE = 3


def _normalize(size):
    return {"L": "large", "M": "medium", "S": "small"}.get(size, str(size).lower())


def _step(size, delta):
    """delta가 +면 잘게, -면 굵게. 양 끝에서는 멈춘다."""
    try:
        idx = SIZE_ORDER.index(_normalize(size))
    except ValueError:
        return _normalize(size)
    return SIZE_ORDER[max(0, min(len(SIZE_ORDER) - 1, idx + delta))]


def empty_skill():
    return {}


def record_done(domain_skill, contexts):
    """완료를 영역별로 기록한다. 숙련도 추정의 유일한 상향 입력이다."""
    for node in contexts or []:
        entry = domain_skill.setdefault(node, {"done": 0, "too_big": 0, "abandon": 0})
        entry["done"] += 1
    return domain_skill


def record_reject(domain_skill, contexts, reason):
    """거부를 영역별로 기록한다.

    **`단서 없음`은 여기서 세지 않는다.** 그것은 분해가 틀렸다는 신호가 아니라
    시작 줄이 틀렸다는 신호이고, 크기를 줄이면 엉뚱한 것을 고치게 된다.
    """
    if reason not in ("너무 큼", "이탈"):
        return domain_skill

    key = "too_big" if reason == "너무 큼" else "abandon"
    for node in contexts or []:
        entry = domain_skill.setdefault(node, {"done": 0, "too_big": 0, "abandon": 0})
        entry[key] += 1
    return domain_skill


def classify(domain_skill, contexts):
    """이 항목의 영역들을 보고 `unfamiliar` / `familiar` / `unknown`을 낸다.

    맥락이 여럿이면 **나쁜 쪽을 따른다.** 하나라도 낯설면 낯선 것으로 본다 —
    굵게 줬다가 못 하는 손해가 잘게 줬다가 지루한 손해보다 크다.
    """
    if not contexts:
        return "unknown"

    seen = [domain_skill.get(node) for node in contexts]
    seen = [s for s in seen if s]
    if not seen:
        return "unknown"

    if any(s["too_big"] + s["abandon"] >= _UNFAMILIAR_FAILS and s["done"] == 0
           for s in seen):
        return "unfamiliar"

    if all(s["done"] >= _FAMILIAR_DONE and s["too_big"] == 0 for s in seen):
        return "familiar"

    return "unknown"


def decide(base_size, domain_skill, contexts, ceiling=None):
    """이 항목에 줄 카드 굵기를 정한다.

    Args:
        base_size: 전역 보정까지 끝난 크기 (`size_calibrator.calibrate`의 결과)
        domain_skill: 영역별 기록
        contexts: 이 항목의 맥락 노드
        ceiling: 캘린더가 정한 상한. **이것은 넘지 못한다.**

    Returns:
        {"size":..., "depth": "shallow|medium|deep", "level":..., "rationale":...}
    """
    level = classify(domain_skill, contexts)

    if level == "unfamiliar":
        size = _step(base_size, +1)
        rationale = "이 맥락에서 아직 완료가 없고 막힌 적이 있어 한 단계 잘게 잡았다"
    elif level == "familiar":
        size = _step(base_size, -1)
        rationale = "이 맥락에서 꾸준히 완료해 왔으므로 굵게 잡았다. 잘게 주면 오히려 방해가 된다"
    else:
        size = _normalize(base_size)
        rationale = "이 맥락의 기록이 아직 판단할 만큼 쌓이지 않아 기본 굵기를 썼다"

    # 캘린더 상한은 숙련도로 뚫지 못한다. 시간이 없는 것은 실력과 무관하다.
    if ceiling:
        try:
            if SIZE_ORDER.index(size) < SIZE_ORDER.index(_normalize(ceiling)):
                size = _normalize(ceiling)
                rationale += " (다음 일정까지 시간이 없어 더 줄임)"
        except ValueError:
            pass

    return {
        "size": size,
        "depth": _depth_of(size),
        "level": level,
        "rationale": rationale,
    }


_DEPTH = {"large": "shallow", "medium": "medium", "small": "deep", "minimal": "deep"}


def _depth_of(size):
    return _DEPTH.get(size, "medium")


def on_stuck(size):
    """`너무 큼`이 눌렸을 때 갈 굵기를 미리 계산해 둔다.

    지금은 눌린 뒤에 한 단계 잘게 다시 쓰는데, 그 '한 단계'를 모델이 매번
    판단하고 있었다. 미리 주면 판단할 것이 없어진다.
    """
    finer = _step(size, +1)
    if finer == _normalize(size):
        return None
    return {
        "signal": "너무 큼을 누르거나, 시작 줄을 읽고도 첫 동작이 떠오르지 않으면",
        "finer_size": finer,
    }
