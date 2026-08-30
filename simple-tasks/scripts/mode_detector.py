"""시각 기반 모드 판정.

현재 시각을 받아 세션 모드와 기본 크기를 반환한다.
"""


def detect(hour: int, minute: int = 0) -> dict:
    """현재 시각으로 모드 반환.

    Returns:
        {"mode": str, "default_size": str, "is_closing": bool}
    """
    if hour < 11:
        return {"mode": "morning", "default_size": "medium", "is_closing": False}
    elif hour < 13:
        return {"mode": "midday", "default_size": "medium", "is_closing": False}
    elif hour < 14:
        return {"mode": "after_lunch", "default_size": "minimal", "is_closing": False}
    elif hour < 16:
        return {"mode": "afternoon", "default_size": "medium", "is_closing": False}
    else:
        return {"mode": "closing", "default_size": "small", "is_closing": True}
