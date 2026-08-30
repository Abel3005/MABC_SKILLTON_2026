"""크기 보정 모듈.

거부 로그 최근 10건을 분석하여 카드 크기를 보정한다.
"""

SIZE_ORDER = ["large", "medium", "small", "minimal"]


def _shrink(size: str) -> str:
    """크기를 한 단계 축소."""
    # 단축 표기 → 전체 표기 변환
    size_map = {"L": "large", "M": "medium", "S": "small"}
    normalized = size_map.get(size, size.lower())

    try:
        idx = SIZE_ORDER.index(normalized)
    except ValueError:
        return normalized

    if idx < len(SIZE_ORDER) - 1:
        return SIZE_ORDER[idx + 1]
    return normalized


def calibrate(rejection_log: list, base_size: str) -> dict:
    """거부 로그 최근 10건 분석하여 크기 보정.

    Args:
        rejection_log: 거부 로그 리스트 (전체)
        base_size: 현재 기본 크기

    Returns:
        {
            "adjusted_size": str,
            "shrunk": bool,
            "items_needing_prep": [str]
        }
    """
    recent = rejection_log[-10:] if len(rejection_log) > 10 else rejection_log

    too_big_count = sum(1 for r in recent if r.get("reason") == "너무 큼")
    abandon_count = sum(1 for r in recent if r.get("reason") == "이탈")

    # 자료 없음 반복 항목 찾기
    no_material_items = {}
    for r in recent:
        if r.get("reason") == "자료 없음":
            name = r.get("name", "")
            no_material_items[name] = no_material_items.get(name, 0) + 1

    items_needing_prep = [name for name, count in no_material_items.items() if count >= 2]

    adjusted = base_size
    shrunk = False

    if too_big_count >= 3:
        adjusted = _shrink(adjusted)
        shrunk = True

    if abandon_count >= 2:
        adjusted = _shrink(adjusted)
        shrunk = True

    return {
        "adjusted_size": adjusted,
        "shrunk": shrunk,
        "items_needing_prep": items_needing_prep,
    }
