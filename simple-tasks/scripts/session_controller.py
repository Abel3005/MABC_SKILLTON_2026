"""세션 컨트롤러.

세션 흐름을 관리한다: 시작, 반응 기록, 라운드 제한, 카드 열기/닫기, 종료.
"""

from datetime import datetime

from . import mode_detector
from . import size_calibrator
from . import priority_engine


def start_session(state: dict, now: datetime | None = None) -> dict:
    """세션 시작: 묵힘 해제, 열린 카드 확인, 모드 판정.

    Returns:
        {
            "has_open_card": bool,
            "open_card": dict | None,
            "open_count": int,
            "today_deadline_count": int,
            "mode": dict,
            "calibration": dict,
            "muted_released": [str]
        }
    """
    # 캘린더가 준 시각이 있으면 그것을 쓴다. 서버 타임존이 사용자와 다르면
    # 로컬 시각으로는 "오늘 마감"과 모드 판정이 둘 다 틀린다.
    now = now or datetime.now()
    mode = mode_detector.detect(now.hour, now.minute)

    # 묵힘 해제
    muted_released = []
    for item in state.get("open_items", []):
        if item.get("muted"):
            item["muted"] = None
            muted_released.append(item["name"])

    # 오늘 마감 개수
    today = now.strftime("%m-%d")
    today_deadline_count = sum(
        1 for item in state.get("open_items", [])
        if item.get("deadline") and item["deadline"].startswith(today)
    )

    # 크기 보정
    calibration = size_calibrator.calibrate(
        state.get("rejection_log", []),
        mode["default_size"],
    )

    return {
        "has_open_card": state.get("open_card") is not None,
        "open_card": state.get("open_card"),
        "open_count": len(state.get("open_items", [])),
        "today_deadline_count": today_deadline_count,
        "mode": mode,
        "calibration": calibration,
        "muted_released": muted_released,
    }


def record_response(state: dict, item_name: str, response: str,
                     reason: str | None = None) -> dict:
    """반응 기록 (즉시 상태 파일에 반영).

    Args:
        state: 현재 상태
        item_name: 항목 이름
        response: "too_big" | "other" | "abandon" | "done_partial"
        reason: 사유 (다른 거 선택 시)

    Returns:
        {"state": dict, "action": str, "too_big_count": int}
    """
    now = datetime.now()
    date_str = now.strftime("%m-%d %H:%M")

    reason_map = {
        "too_big": "너무 큼",
        "other": reason or "그냥 아님",
        "abandon": "이탈",
        "done_partial": "하다 말았음",
    }
    reason_text = reason_map.get(response, response)

    state.setdefault("rejection_log", []).append({
        "date": date_str,
        "name": item_name,
        "reason": reason_text,
    })

    # too_big_count 추적 (해당 항목의 최근 연속 "너무 큼" 횟수).
    # 다른 항목의 로그는 건너뛴다. --next 로 카드가 이어지면 그 사이에 다른 항목의
    # 거부가 끼어드는데, 그것 때문에 카운트가 리셋되면 "2회까지만" 규칙이 무너진다.
    too_big_count = 0
    for log in reversed(state.get("rejection_log", [])):
        if log["name"] != item_name:
            continue
        if log["reason"] == "너무 큼":
            too_big_count += 1
        else:
            break

    return {
        "state": state,
        "action": f"recorded_{response}",
        "too_big_count": too_big_count,
    }


def check_round_limit(round_count: int) -> bool:
    """3회 초과 체크. True면 제한 도달."""
    return round_count >= 3


def open_card(state: dict, item_name: str) -> dict:
    """카드 열기."""
    now = datetime.now()
    state["open_card"] = {
        "name": item_name,
        "opened_at": now.strftime("%m-%d %H:%M"),
    }
    return {"state": state, "action": "card_opened", "item": item_name}


def close_card(state: dict) -> dict:
    """카드 닫기."""
    closed = state.get("open_card")
    state["open_card"] = None
    return {"state": state, "action": "card_closed", "closed_item": closed}


def end_session(state: dict) -> dict:
    """세션 종료 정리."""
    # 열린 카드가 있으면 그대로 남김 (다음 세션에서 이탈 회수)
    return {
        "state": state,
        "action": "session_ended",
        "open_card_remaining": state.get("open_card"),
    }
