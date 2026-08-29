"""수확 처리기.

완료된 항목의 재진입 메모와 새 아이디어를 처리한다.
"""

from datetime import datetime


def harvest(state: dict, item_name: str,
            reentry_note: str | None = None,
            new_idea: str | None = None) -> dict:
    """수확 처리.

    Args:
        state: 현재 상태 dict
        item_name: 완료 대상 항목 이름
        reentry_note: 재진입 메모 (비어있으면 완료 처리)
        new_idea: 하다가 떠오른 것 (있으면 새 항목 추가)

    Returns:
        {"action": str, "state": dict, "completed_item": str | None, "new_item": str | None}
    """
    now = datetime.now()
    result = {
        "action": "",
        "state": state,
        "completed_item": None,
        "new_item": None,
    }

    # 해당 항목 찾기
    target_idx = None
    for i, item in enumerate(state.get("open_items", [])):
        if item["name"] == item_name:
            target_idx = i
            break

    if target_idx is None:
        # 항목이 없으면 완료 처리만 (콜드 스타트 등)
        if not reentry_note:
            state.setdefault("completed", []).append({
                "name": item_name,
                "completed_at": now.strftime("%m-%d %H:%M"),
            })
            result["action"] = "completed_new"
            result["completed_item"] = item_name
        else:
            # 새 항목으로 추가
            state.setdefault("open_items", []).append({
                "name": item_name,
                "deadline": None,
                "size": "M",
                "blocking": 0,
                "reentry": reentry_note,
                "muted": None,
                "needs_prep": False,
            })
            result["action"] = "added_with_reentry"
    else:
        target = state["open_items"][target_idx]

        if not reentry_note:
            # 완료 처리: open_items에서 제거 → completed로 이동
            state["open_items"].pop(target_idx)
            state.setdefault("completed", []).append({
                "name": item_name,
                "completed_at": now.strftime("%m-%d %H:%M"),
            })
            result["action"] = "completed"
            result["completed_item"] = item_name
        else:
            # 재진입 메모 업데이트
            target["reentry"] = reentry_note
            result["action"] = "reentry_updated"

    # 새 아이디어 추가
    if new_idea:
        today = now.strftime("%m-%d")
        new_item = {
            "name": new_idea,
            "deadline": None,
            "size": "M",
            "blocking": 0,
            "reentry": None,
            "muted": f"{today} 수확",
            "needs_prep": False,
        }
        state.setdefault("open_items", []).append(new_item)
        result["new_item"] = new_idea

    result["state"] = state
    return result
